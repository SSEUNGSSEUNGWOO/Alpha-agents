"""
Out-of-Sample 검증
- 앞 60일로 학습, 뒤 30일로 테스트
- 백테스트 엔진으로 실제 수익률까지 확인
Usage: PYTHONPATH=. python3 scripts/oos_validation.py
"""
import asyncio
import sys
sys.path.insert(0, ".")

import pickle
import numpy as np
import xgboost as xgb
import pandas as pd
from sklearn.metrics import classification_report
from sklearn.utils.class_weight import compute_sample_weight
from pathlib import Path

from agents.strategy_agent.feature_builder import build_training_data, add_multi_tf_features, add_btc_features, FEATURE_COLS, BTC_FEATURE_COLS
from agents.analysis_agent.technical import compute_indicators
from storage import init_db, get_pool

COMMISSION = 0.0004
SLIPPAGE   = 0.0002


async def fetch_all(symbol: str, interval: str) -> pd.DataFrame:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT open_time, open, high, low, close, volume FROM ohlcv "
            "WHERE symbol=$1 AND interval=$2 ORDER BY open_time ASC",
            symbol, interval,
        )
    df = pd.DataFrame(rows, columns=["open_time","open","high","low","close","volume"])
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"])
    return df


def strip_tz(df):
    df["open_time"] = pd.to_datetime(df["open_time"]).dt.tz_convert("UTC").dt.tz_localize(None)
    return df


def simple_backtest(df: pd.DataFrame, model, feat_cols=None, initial_capital=1000.0, confidence_threshold=0.45, max_position_ratio=0.25) -> dict:
    from agents.strategy_agent.xgb_model import LABEL_MAP
    if feat_cols is None:
        feat_cols = FEATURE_COLS
    cash = initial_capital
    position = 0.0
    entry_price = 0.0
    trades = []
    equity = []

    for _, row in df.iterrows():
        price = row["close"]
        features = pd.DataFrame([{col: row[col] for col in feat_cols}])
        proba = model.predict_proba(features)[0]
        pred_idx = int(np.argmax(proba))
        action = LABEL_MAP[pred_idx]
        confidence = float(proba[pred_idx])

        equity_now = cash + position * price
        equity.append(equity_now)

        if action == "BUY" and confidence >= confidence_threshold and position == 0:
            spend = equity_now * max_position_ratio
            buy_price = price * (1 + SLIPPAGE)
            fee = spend * COMMISSION
            qty = (spend - fee) / buy_price
            cash -= qty * buy_price + fee
            position = qty
            entry_price = buy_price
            trades.append({"type": "BUY", "price": buy_price})

        elif action == "SELL" and confidence >= confidence_threshold and position > 0:
            sell_price = price * (1 - SLIPPAGE)
            proceeds = position * sell_price
            fee = proceeds * COMMISSION
            pnl = proceeds - fee - (position * entry_price)
            cash += proceeds - fee
            trades.append({"type": "SELL", "price": sell_price, "pnl": pnl})
            position = 0.0

    if position > 0:
        cash += position * df.iloc[-1]["close"] * (1 - SLIPPAGE) * (1 - COMMISSION)

    equity_s = pd.Series(equity)
    returns  = equity_s.pct_change().dropna()
    peak     = equity_s.cummax()
    mdd      = float(((equity_s - peak) / peak).min()) * -1

    pnls     = [t["pnl"] for t in trades if "pnl" in t]
    win_rate = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0

    return {
        "final":      round(cash, 2),
        "return_pct": round((cash - initial_capital) / initial_capital * 100, 2),
        "mdd":        round(mdd * 100, 2),
        "trades":     len([t for t in trades if t["type"] == "BUY"]),
        "win_rate":   round(win_rate * 100, 1),
    }


async def validate(symbol: str):
    print(f"\n{'='*50}")
    print(f"  OOS 검증: {symbol}")
    print(f"{'='*50}")

    df_15m = strip_tz(compute_indicators(await fetch_all(symbol, "15m")))
    df_1h  = strip_tz(compute_indicators(await fetch_all(symbol, "1h")))
    df_4h  = strip_tz(compute_indicators(await fetch_all(symbol, "4h")))

    df = add_multi_tf_features(df_15m, df_1h, df_4h)

    feat_cols = BTC_FEATURE_COLS if symbol == "BTCUSDT" else FEATURE_COLS
    if symbol != "BTCUSDT":
        btc_15m = strip_tz(compute_indicators(await fetch_all("BTCUSDT", "15m")))
        btc_1h  = strip_tz(compute_indicators(await fetch_all("BTCUSDT", "1h")))
        df = add_btc_features(df, btc_15m, btc_1h)

    # 레이블 붙이기
    HORIZON = 32
    THRESHOLD = 0.01
    df["future_return"] = df["close"].shift(-HORIZON) / df["close"] - 1
    df["label"] = "HOLD"
    df.loc[df["future_return"] >  THRESHOLD, "label"] = "BUY"
    df.loc[df["future_return"] < -THRESHOLD, "label"] = "SELL"
    df = df.dropna(subset=feat_cols + ["future_return"]).reset_index(drop=True)

    # 60일 / 30일 분리
    split_date = df["open_time"].max() - pd.Timedelta(days=30)
    train_df = df[df["open_time"] < split_date].copy()
    test_df  = df[df["open_time"] >= split_date].copy()

    print(f"학습: {train_df['open_time'].min().date()} ~ {train_df['open_time'].max().date()} ({len(train_df)}개)")
    print(f"테스트: {test_df['open_time'].min().date()} ~ {test_df['open_time'].max().date()} ({len(test_df)}개)")

    X_train = train_df[feat_cols]
    y_train = train_df["label"].map({"BUY": 2, "HOLD": 1, "SELL": 0})
    X_test  = test_df[feat_cols]
    y_test  = test_df["label"].map({"BUY": 2, "HOLD": 1, "SELL": 0})

    # 학습
    sample_weights = compute_sample_weight("balanced", y_train)
    model = xgb.XGBClassifier(
        n_estimators=500, learning_rate=0.05, max_depth=6,
        subsample=0.8, colsample_bytree=0.8,
        eval_metric="mlogloss", early_stopping_rounds=30,
        random_state=42, n_jobs=-1,
    )
    X_tr, X_val = X_train[:int(len(X_train)*0.85)], X_train[int(len(X_train)*0.85):]
    y_tr, y_val = y_train[:int(len(y_train)*0.85)], y_train[int(len(y_train)*0.85):]
    model.fit(X_tr, y_tr, sample_weight=compute_sample_weight("balanced", y_tr),
              eval_set=[(X_val, y_val)], verbose=False)

    # 분류 성능
    y_pred = model.predict(X_test)
    print(f"\n[분류 성능 — OOS 30일]")
    print(classification_report(y_test, y_pred, target_names=["SELL","HOLD","BUY"]))

    # 백테스트 성능
    result = simple_backtest(test_df, model, feat_cols=feat_cols, confidence_threshold=0.40)
    print(f"[백테스트 성능 — OOS 30일]")
    print(f"  수익률:  {result['return_pct']:+.2f}%")
    print(f"  MDD:     -{result['mdd']:.2f}%")
    print(f"  거래:    {result['trades']}회")
    print(f"  승률:    {result['win_rate']}%")

    # 피처 중요도 Top 10
    importance = pd.Series(model.feature_importances_, index=feat_cols).sort_values(ascending=False)
    print(f"\n[피처 중요도 Top 10]")
    for feat, score in importance.head(10).items():
        bar = "█" * int(score * 200)
        print(f"  {feat:<25} {bar} {score:.4f}")


async def main():
    await init_db()
    from config import settings
    for symbol in settings.symbols:
        await validate(symbol)


if __name__ == "__main__":
    asyncio.run(main())
