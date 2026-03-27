"""
백테스트 엔진
- DB에서 과거 OHLCV 로드
- XGBoost 예측으로 BUY/SELL/HOLD 시뮬레이션
- 수수료, 슬리피지 적용
- 결과: 수익률, MDD, 승률, Sharpe
"""
import asyncio
import pandas as pd
import numpy as np
from agents.strategy_agent.feature_builder import build_training_data, add_multi_tf_features, FEATURE_COLS
from agents.strategy_agent.xgb_model import predict
from agents.analysis_agent.technical import compute_indicators
from storage import get_pool


COMMISSION = 0.0004   # 0.04% Binance maker fee
SLIPPAGE   = 0.0002   # 0.02% 슬리피지 근사


async def _fetch_all(symbol: str, interval: str) -> pd.DataFrame:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT open_time, open, high, low, close, volume
            FROM ohlcv WHERE symbol=$1 AND interval=$2
            ORDER BY open_time ASC
            """,
            symbol, interval,
        )
    df = pd.DataFrame(rows, columns=["open_time","open","high","low","close","volume"])
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"])
    return df


def _strip_tz(df: pd.DataFrame) -> pd.DataFrame:
    df["open_time"] = pd.to_datetime(df["open_time"]).dt.tz_convert("UTC").dt.tz_localize(None)
    return df


async def run_backtest(
    symbol: str,
    initial_capital: float = 10_000.0,
    confidence_threshold: float = 0.45,
    max_position_ratio: float = 0.25,
) -> dict:
    # 데이터 로드 & 피처 빌드
    df_15m = _strip_tz(compute_indicators(await _fetch_all(symbol, "15m")))
    df_1h  = _strip_tz(compute_indicators(await _fetch_all(symbol, "1h")))
    df_4h  = _strip_tz(compute_indicators(await _fetch_all(symbol, "4h")))

    df = add_multi_tf_features(df_15m, df_1h, df_4h)
    df = df.dropna(subset=FEATURE_COLS).reset_index(drop=True)

    # 시뮬레이션
    cash      = initial_capital
    position  = 0.0   # 보유 코인 수량
    trades    = []
    equity    = []

    for _, row in df.iterrows():
        price = row["close"]
        signals = {col: row[col] for col in FEATURE_COLS}
        result  = predict(symbol, signals)

        action     = result["action"]
        confidence = result["confidence"]
        equity_now = cash + position * price
        equity.append({"open_time": row["open_time"], "equity": equity_now})

        if action == "BUY" and confidence >= confidence_threshold and position == 0:
            spend = equity_now * max_position_ratio
            buy_price = price * (1 + SLIPPAGE)
            fee = spend * COMMISSION
            qty = (spend - fee) / buy_price
            cash -= (qty * buy_price + fee)
            position = qty
            trades.append({"type": "BUY", "time": row["open_time"],
                           "price": buy_price, "qty": qty})

        elif action == "SELL" and confidence >= confidence_threshold and position > 0:
            sell_price = price * (1 - SLIPPAGE)
            proceeds = position * sell_price
            fee = proceeds * COMMISSION
            cash += proceeds - fee
            trades.append({"type": "SELL", "time": row["open_time"],
                           "price": sell_price, "qty": position,
                           "pnl": proceeds - fee - (position * trades[-1]["price"] if trades else 0)})
            position = 0.0

    # 마지막 포지션 청산
    if position > 0:
        last_price = df.iloc[-1]["close"]
        cash += position * last_price * (1 - SLIPPAGE) * (1 - COMMISSION)
        position = 0.0

    # 성과 계산
    equity_series = pd.DataFrame(equity).set_index("open_time")["equity"]
    total_return  = (cash - initial_capital) / initial_capital
    returns       = equity_series.pct_change().dropna()
    mdd           = _max_drawdown(equity_series)
    sharpe        = _sharpe(returns)

    buy_trades  = [t for t in trades if t["type"] == "BUY"]
    sell_trades = [t for t in trades if t["type"] == "SELL"]
    pnls        = [t["pnl"] for t in sell_trades if "pnl" in t]
    win_rate    = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0.0

    return {
        "symbol":         symbol,
        "initial_capital": initial_capital,
        "final_capital":  round(cash, 2),
        "total_return":   round(total_return * 100, 2),   # %
        "mdd":            round(mdd * 100, 2),             # %
        "sharpe":         round(sharpe, 3),
        "num_trades":     len(buy_trades),
        "win_rate":       round(win_rate * 100, 2),        # %
        "trades":         trades,
    }


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    drawdown = (equity - peak) / peak
    return float(drawdown.min()) * -1


def _sharpe(returns: pd.Series, periods_per_year: int = 35040) -> float:
    # 15분봉 기준 연간화: 4 * 24 * 365 = 35040
    if returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(periods_per_year))
