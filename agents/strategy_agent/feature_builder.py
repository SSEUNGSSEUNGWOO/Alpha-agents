import pandas as pd
import numpy as np
import talib
from agents.strategy_agent.labeler import build_labeled_dataset
from agents.analysis_agent.technical import compute_indicators


FEATURE_COLS = [
    # 4h — 예측력 핵심
    "rsi_4h", "macd_hist_4h", "bb_position_4h", "bb_width_4h", "ema_cross_4h",
    # 1h
    "rsi_1h", "macd_hist_1h", "bb_position_1h", "ema_cross_1h",
    # 15m — 변동성/모멘텀 위주로 정리
    "bb_width_15m", "atr_15m", "volatility_15m", "adx_15m",
    "volume_ratio_15m", "stoch_k_15m",
]


def add_multi_tf_features(df_15m: pd.DataFrame,
                           df_1h: pd.DataFrame,
                           df_4h: pd.DataFrame) -> pd.DataFrame:
    df = df_15m.copy()

    # 15m 지표
    df["rsi_15m"]        = df["rsi_14"]
    df["macd_15m"]       = df["macd"]
    df["macd_hist_15m"]  = df["macd_hist"]
    df["bb_position_15m"]= df["bb_position"]
    df["bb_width_15m"]   = df["bb_width"]
    df["volume_ratio_15m"]= df["volume_ratio"]
    df["atr_15m"]        = df["atr_14"]
    df["volatility_15m"] = df["volatility"]
    df["stoch_k_15m"]    = df["stoch_k"]
    df["adx_15m"]        = df["adx_14"]
    df["ema_cross_15m"]  = (df["ema_20"] > df["ema_50"]).astype(float)

    # 1h 지표 → 15m에 merge_asof (Binance 1h 캔들 경계가 항상 정각은 아닐 수 있음)
    df_1h_slim = df_1h[["open_time", "rsi_14", "macd_hist", "bb_position", "ema_20", "ema_50"]].copy()
    df_1h_slim.columns = ["open_time", "rsi_1h", "macd_hist_1h", "bb_position_1h", "ema20_1h", "ema50_1h"]
    df = pd.merge_asof(
        df.sort_values("open_time"),
        df_1h_slim.sort_values("open_time"),
        on="open_time",
        direction="backward",
    )
    df["ema_cross_1h"] = (df["ema20_1h"] > df["ema50_1h"]).astype(float)

    # 4h 지표 → 15m에 merge_asof
    df_4h["ema_cross_4h_raw"] = (df_4h["ema_20"] > df_4h["ema_50"]).astype(float)
    df_4h_slim = df_4h[["open_time", "rsi_14", "macd_hist", "bb_position", "bb_width", "ema_cross_4h_raw"]].copy()
    df_4h_slim.columns = ["open_time", "rsi_4h", "macd_hist_4h", "bb_position_4h", "bb_width_4h", "ema_cross_4h"]
    df = pd.merge_asof(
        df.sort_values("open_time"),
        df_4h_slim.sort_values("open_time"),
        on="open_time",
        direction="backward",
    )

    return df


async def build_training_data(symbol: str) -> tuple[pd.DataFrame, pd.Series]:
    from storage import get_pool
    pool = await get_pool()

    async def fetch(interval: str, limit: int = 10000) -> pd.DataFrame:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT open_time, open, high, low, close, volume
                FROM ohlcv WHERE symbol=$1 AND interval=$2
                ORDER BY open_time ASC LIMIT $3
                """,
                symbol, interval, limit,
            )
        df = pd.DataFrame(rows, columns=["open_time","open","high","low","close","volume"])
        for c in ["open","high","low","close","volume"]:
            df[c] = df[c].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"])
        return compute_indicators(df)

    df_15m = await fetch("15m")
    df_1h  = await fetch("1h")
    df_4h  = await fetch("4h")

    # 레이블 붙이기
    labeled = await build_labeled_dataset(symbol)
    labeled["open_time"] = pd.to_datetime(labeled["open_time"])

    # open_time timezone 제거 (UTC → naive)
    def strip_tz(df):
        df["open_time"] = pd.to_datetime(df["open_time"]).dt.tz_convert("UTC").dt.tz_localize(None)
        return df
    df_15m  = strip_tz(df_15m)
    df_1h   = strip_tz(df_1h)
    df_4h   = strip_tz(df_4h)
    labeled = strip_tz(labeled)

    df = add_multi_tf_features(df_15m, df_1h, df_4h)
    df = df.merge(labeled[["open_time", "label"]], on="open_time", how="inner")
    df = df.dropna(subset=FEATURE_COLS)

    X = df[FEATURE_COLS]
    y = df["label"].map({"BUY": 2, "HOLD": 1, "SELL": 0})

    print(f"[{symbol}] 학습 데이터: {len(X)}개, 피처: {len(FEATURE_COLS)}개")
    return X, y
