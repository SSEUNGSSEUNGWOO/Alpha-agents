import asyncio
import pickle
from pathlib import Path
import numpy as np
import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
from sklearn.utils.class_weight import compute_sample_weight
from agents.strategy_agent.feature_builder import build_training_data

MODEL_DIR = Path("models")
MODEL_DIR.mkdir(exist_ok=True)


async def train(symbol: str) -> None:
    print(f"\n{'='*50}")
    print(f"  XGBoost 학습 시작: {symbol}")
    print(f"{'='*50}")

    X, y = await build_training_data(symbol)

    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.15, shuffle=False  # 시계열이라 shuffle=False
    )

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

    sample_weights = compute_sample_weight("balanced", y_train)

    model.fit(
        X_train, y_train,
        sample_weight=sample_weights,
        eval_set=[(X_val, y_val)],
        verbose=50,
    )

    y_pred = model.predict(X_val)
    print(f"\n[{symbol}] 검증 결과:")
    print(classification_report(y_val, y_pred, target_names=["SELL", "HOLD", "BUY"]))

    # 모델 저장
    model_path = MODEL_DIR / f"xgb_{symbol.lower()}.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    print(f"모델 저장: {model_path}")


async def main():
    from config import settings
    for symbol in settings.symbols:
        await train(symbol)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    asyncio.run(main())
