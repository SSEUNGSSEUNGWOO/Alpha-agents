import pandas as pd
import numpy as np
import talib
from storage import get_pool  # noqa: F401 (used in get_fear_greed)


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


async def get_fear_greed() -> float:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM onchain WHERE symbol='BTC' AND metric='fear_greed' ORDER BY date DESC LIMIT 1"
            )
        return float(row["value"]) if row else 50.0
    except Exception:
        return 50.0


async def get_google_trends(symbol: str) -> tuple[float, float]:
    """최신 Google Trends 값과 전주 대비 변화율 반환"""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT value FROM onchain
                WHERE symbol=$1 AND metric='google_trends'
                ORDER BY date DESC LIMIT 2
                """,
                symbol,
            )
        if not rows:
            return 50.0, 0.0
        score  = float(rows[0]["value"])
        change = score - float(rows[1]["value"]) if len(rows) >= 2 else 0.0
        return score, change
    except Exception:
        return 50.0, 0.0


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

    trends_score, trends_change = await get_google_trends(symbol)

    signals = {
        # 15m 지표
        "rsi_15m":         last(df_15m, "rsi_14"),
        "macd_15m":        last(df_15m, "macd"),
        "macd_hist_15m":   last(df_15m, "macd_hist"),
        "bb_position_15m": last(df_15m, "bb_position"),
        "bb_width_15m":    last(df_15m, "bb_width"),
        "volume_ratio_15m":last(df_15m, "volume_ratio"),
        "atr_15m":         last(df_15m, "atr_14"),
        "volatility_15m":  last(df_15m, "volatility"),
        "adx_15m":         last(df_15m, "adx_14"),
        "stoch_k_15m":     last(df_15m, "stoch_k"),
        # 1h 지표
        "rsi_1h":          last(df_1h, "rsi_14"),
        "macd_hist_1h":    last(df_1h, "macd_hist"),
        "bb_position_1h":  last(df_1h, "bb_position"),
        "ema_cross_1h":    1.0 if last(df_1h, "ema_20") > last(df_1h, "ema_50") else 0.0,
        # 4h 지표
        "rsi_4h":          last(df_4h, "rsi_14"),
        "macd_hist_4h":    last(df_4h, "macd_hist"),
        "bb_position_4h":  last(df_4h, "bb_position"),
        "bb_width_4h":     last(df_4h, "bb_width"),
        "ema_cross_4h":    1.0 if last(df_4h, "ema_20") > last(df_4h, "ema_50") else 0.0,
        # 시장 심리
        "fear_greed":      await get_fear_greed(),
        "trends_score":    trends_score,
        "trends_change":   trends_change,
    }

    # BTC 시장 피처 (BTC 자신 제외)
    if symbol != "BTCUSDT":
        btc_15m = await fetch_ohlcv("BTCUSDT", "15m", limit=100)
        btc_1h  = await fetch_ohlcv("BTCUSDT", "1h",  limit=10)

        btc_ret_15m = float(btc_15m["close"].pct_change().iloc[-1]) if len(btc_15m) >= 2 else 0.0
        btc_ret_1h  = float(btc_1h["close"].pct_change().iloc[-1])  if len(btc_1h)  >= 2 else 0.0

        sym_close = df_15m["close"].iloc[-1]
        btc_close = float(btc_15m["close"].iloc[-1]) if not btc_15m.empty else sym_close
        btc_eth_ratio = btc_close / sym_close if sym_close != 0 else 1.0

        # 24h 롤링 상관관계 (96봉)
        sym_ret  = df_15m["close"].pct_change().tail(96)
        btc_ret  = btc_15m["close"].pct_change().tail(96)
        if len(sym_ret) >= 10 and len(btc_ret) >= 10:
            min_len = min(len(sym_ret), len(btc_ret))
            corr = float(np.corrcoef(sym_ret.values[-min_len:], btc_ret.values[-min_len:])[0, 1])
            corr = corr if not np.isnan(corr) else 0.0
        else:
            corr = 0.0

        signals.update({
            "btc_return_15m":  btc_ret_15m,
            "btc_return_1h":   btc_ret_1h,
            "btc_eth_ratio":   btc_eth_ratio,
            "btc_eth_corr_24h":corr,
        })

    return signals
