import asyncio
import pickle
from pathlib import Path
import numpy as np
import xgboost as xgb
from sklearn.metrics import classification_report, f1_score, accuracy_score
from sklearn.utils.class_weight import compute_sample_weight
from agents.strategy_agent.feature_builder import build_training_data

MODEL_DIR = Path("models")
MODEL_DIR.mkdir(exist_ok=True)

HALFLIFE_DAYS = 180  # 180일 전 데이터 = 현재의 50% 가중치

# 시계열 기준 데이터 분할 비율
TRAIN_RATIO = 0.70
VAL_RATIO   = 0.15
# TEST_RATIO  = 0.15 (나머지)


def time_decay_weights(open_times, halflife_days: int = HALFLIFE_DAYS) -> np.ndarray:
    """
    최근 데이터일수록 높은 가중치 (지수 감쇠)
    halflife_days 전 데이터 → 가중치 0.5
    """
    times    = open_times.reset_index(drop=True)
    latest   = times.max()
    days_old = (latest - times).dt.total_seconds() / 86400
    weights  = np.exp(-days_old.values * np.log(2) / halflife_days)
    return weights / weights.mean()  # 평균 1로 정규화


def time_split(X, y, open_times):
    """
    시계열 순서를 유지한 3-way split
    미래 데이터가 과거 학습에 유입되는 look-ahead bias 방지
    """
    n       = len(X)
    i_train = int(n * TRAIN_RATIO)
    i_val   = int(n * (TRAIN_RATIO + VAL_RATIO))

    X_train = X.iloc[:i_train]
    X_val   = X.iloc[i_train:i_val]
    X_test  = X.iloc[i_val:]

    y_train = y.iloc[:i_train]
    y_val   = y.iloc[i_train:i_val]
    y_test  = y.iloc[i_val:]

    t_train = open_times.iloc[:i_train]
    t_val   = open_times.iloc[i_train:i_val]
    t_test  = open_times.iloc[i_val:]

    return X_train, X_val, X_test, y_train, y_val, y_test, t_train, t_val, t_test


async def train(symbol: str) -> dict:
    print(f"\n{'='*50}")
    print(f"  XGBoost 학습 시작: {symbol}")
    print(f"{'='*50}")

    X, y, open_times = await build_training_data(symbol)

    X_train, X_val, X_test, y_train, y_val, y_test, t_train, t_val, t_test = \
        time_split(X, y, open_times)

    print(f"  Train:      {len(X_train):>5}개  ({t_train.min().date()} ~ {t_train.max().date()})")
    print(f"  Validation: {len(X_val):>5}개  ({t_val.min().date()} ~ {t_val.max().date()})")
    print(f"  Test:       {len(X_test):>5}개  ({t_test.min().date()} ~ {t_test.max().date()})")

    model = xgb.XGBClassifier(
        n_estimators=500,
        learning_rate=0.05,
        max_depth=6,
        subsample=0.8,
        colsample_bytree=0.8,
        use_label_encoder=False,
        eval_metric="mlogloss",
        early_stopping_rounds=30,
        random_state=42,
        n_jobs=-1,
    )

    # 클래스 균형 × 시간 감쇠 결합 (train 셋에만 적용)
    class_w  = compute_sample_weight("balanced", y_train)
    time_w   = time_decay_weights(t_train)
    combined = class_w * time_w
    combined /= combined.mean()

    model.fit(
        X_train, y_train,
        sample_weight=combined,
        eval_set=[(X_val, y_val)],   # early stopping용 (학습에 미사용)
        verbose=50,
    )

    # Validation 결과
    y_val_pred = model.predict(X_val)
    print(f"\n[{symbol}] Validation 결과 (early stopping 기준):")
    print(classification_report(y_val, y_val_pred, target_names=["SELL", "HOLD", "BUY"]))

    # Test 결과 (학습에 전혀 사용 안 한 unseen 데이터)
    y_test_pred = model.predict(X_test)
    test_acc = accuracy_score(y_test, y_test_pred)
    test_f1  = f1_score(y_test, y_test_pred, average="macro")
    print(f"[{symbol}] Test 결과 (unseen — 실제 성능 지표):")
    print(classification_report(y_test, y_test_pred, target_names=["SELL", "HOLD", "BUY"]))
    print(f"  Test Accuracy: {test_acc:.3f} | Test F1 (macro): {test_f1:.3f}")

    model_path = MODEL_DIR / f"xgb_{symbol.lower()}.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    print(f"모델 저장: {model_path}")

    return {"symbol": symbol, "test_acc": test_acc, "test_f1": test_f1}


async def main():
    from config import settings
    results = []
    for symbol in settings.symbols:
        result = await train(symbol)
        results.append(result)

    print(f"\n{'='*50}")
    print("  전체 모델 Test 성능 요약")
    print(f"{'='*50}")
    for r in results:
        print(f"  {r['symbol']:<12} acc={r['test_acc']:.3f}  f1={r['test_f1']:.3f}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    asyncio.run(main())
