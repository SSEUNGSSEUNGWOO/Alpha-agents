import pandas as pd
import numpy as np
import talib
from storage import get_pool


async def fetch_ohlcv(symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT open_time, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol = $1 AND interval = $2
            ORDER BY open_time DESC
            LIMIT $3
            """,
            symbol, interval, limit,
        )
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
    df = df.sort_values("open_time").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"].values
    high  = df["high"].values
    low   = df["low"].values
    volume = df["volume"].values

    df["rsi_14"]       = talib.RSI(close, timeperiod=14)
    df["macd"], df["macd_signal"], df["macd_hist"] = talib.MACD(close)
    df["bb_upper"], df["bb_mid"], df["bb_lower"]   = talib.BBANDS(close, timeperiod=20)
    df["ema_20"]       = talib.EMA(close, timeperiod=20)
    df["ema_50"]       = talib.EMA(close, timeperiod=50)
    df["ema_200"]      = talib.EMA(close, timeperiod=200)
    df["atr_14"]       = talib.ATR(high, low, close, timeperiod=14)
    df["obv"]          = talib.OBV(close, volume)
    df["adx_14"]       = talib.ADX(high, low, close, timeperiod=14)
    df["stoch_k"], df["stoch_d"] = talib.STOCH(high, low, close)

    # 파생 피처
    df["bb_width"]     = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    df["bb_position"]  = (close - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])
    df["volume_ratio"] = df["volume"] / df["volume"].rolling(20).mean()
    df["price_change"] = df["close"].pct_change()
    df["volatility"]   = df["price_change"].rolling(14).std()

    return df


async def get_technical_signals(symbol: str) -> dict:
    df_15m = await fetch_ohlcv(symbol, "15m", limit=300)
    df_1h  = await fetch_ohlcv(symbol, "1h",  limit=300)
    df_4h  = await fetch_ohlcv(symbol, "4h",  limit=300)

    df_15m = compute_indicators(df_15m)
    df_1h  = compute_indicators(df_1h)
    df_4h  = compute_indicators(df_4h)

    def last(df: pd.DataFrame, col: str) -> float:
        if df.empty or col not in df.columns or len(df) == 0:
            return 0.0
        val = df[col].iloc[-1]
        return float(val) if not np.isnan(val) else 0.0

    return {
        # 15m 지표
        "rsi_15m":        last(df_15m, "rsi_14"),
        "macd_15m":       last(df_15m, "macd"),
        "macd_hist_15m":  last(df_15m, "macd_hist"),
        "bb_position_15m":last(df_15m, "bb_position"),
        "bb_width_15m":   last(df_15m, "bb_width"),
        "volume_ratio_15m":last(df_15m, "volume_ratio"),
        "atr_15m":        last(df_15m, "atr_14"),
        "volatility_15m": last(df_15m, "volatility"),
        # 1h 지표
        "rsi_1h":         last(df_1h, "rsi_14"),
        "macd_hist_1h":   last(df_1h, "macd_hist"),
        "adx_1h":         last(df_1h, "adx_14"),
        "bb_position_1h": last(df_1h, "bb_position"),
        "ema_trend_1h":   1.0 if last(df_1h, "ema_20") > last(df_1h, "ema_50") else -1.0,
        # 4h 지표
        "rsi_4h":         last(df_4h, "rsi_14"),
        "macd_hist_4h":   last(df_4h, "macd_hist"),
        "adx_4h":         last(df_4h, "adx_14"),
        "ema_trend_4h":   1.0 if last(df_4h, "ema_20") > last(df_4h, "ema_50") else -1.0,
        "bb_position_4h": last(df_4h, "bb_position"),
    }
