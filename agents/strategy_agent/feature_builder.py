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
    # BTC 시장 피처 (ETH에 특히 유효, BTC도 자기 자신과 비교)
    "btc_return_15m", "btc_return_1h", "btc_eth_ratio", "btc_eth_corr_24h",
]

BTC_FEATURE_COLS = [c for c in FEATURE_COLS if not c.startswith("btc_")]


def add_btc_features(df: pd.DataFrame, df_btc_15m: pd.DataFrame, df_btc_1h: pd.DataFrame) -> pd.DataFrame:
    """BTC 시장 피처 추가 — ETH 예측 보조"""
    btc = df_btc_15m[["open_time", "close"]].copy()
    btc["btc_return_15m"] = btc["close"].pct_change()

    btc_1h = df_btc_1h[["open_time", "close"]].copy()
    btc_1h["btc_return_1h"] = btc_1h["close"].pct_change()
    btc_1h = btc_1h.rename(columns={"open_time": "open_time_btc1h"})

    # merge btc 15m returns
    df = pd.merge_asof(
        df.sort_values("open_time"),
        btc[["open_time", "close", "btc_return_15m"]].sort_values("open_time"),
        on="open_time", direction="backward",
    )
    df = df.rename(columns={"close_x": "close", "close_y": "btc_close"}) if "close_x" in df.columns else df
    if "btc_close" not in df.columns:
        df["btc_close"] = df["close"]  # BTC 자기 자신이면 동일

    # merge btc 1h returns
    df["open_time_1h_floor"] = df["open_time"].dt.floor("1h")
    btc_1h_slim = btc_1h.rename(columns={"open_time_btc1h": "open_time_1h_floor"})
    df = df.merge(btc_1h_slim[["open_time_1h_floor", "btc_return_1h"]], on="open_time_1h_floor", how="left")

    # BTC/ETH 비율 (BTC끼리면 1.0)
    if "btc_close" in df.columns and "close" in df.columns:
        df["btc_eth_ratio"] = df["btc_close"] / df["close"]
    else:
        df["btc_eth_ratio"] = 1.0

    # 24h 롤링 상관관계 (96봉 × 15m)
    btc_ret = btc.set_index("open_time")["btc_return_15m"].reindex(df["open_time"])
    sym_ret = df["close"].pct_change().values
    df["btc_eth_corr_24h"] = pd.Series(sym_ret).rolling(96).corr(
        pd.Series(btc_ret.values)
    ).values

    return df


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

    async def fetch(interval: str, limit: int = 10000, sym: str = None) -> pd.DataFrame:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT open_time, open, high, low, close, volume
                FROM ohlcv WHERE symbol=$1 AND interval=$2
                ORDER BY open_time ASC LIMIT $3
                """,
                sym or symbol, interval, limit,
            )
        df = pd.DataFrame(rows, columns=["open_time","open","high","low","close","volume"])
        for c in ["open","high","low","close","volume"]:
            df[c] = df[c].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"])
        return compute_indicators(df)

    df_15m = await fetch("15m")
    df_1h  = await fetch("1h")
    df_4h  = await fetch("4h")

    # BTC 데이터 (시장 피처용)
    def strip_tz(df):
        df["open_time"] = pd.to_datetime(df["open_time"]).dt.tz_convert("UTC").dt.tz_localize(None)
        return df

    if symbol != "BTCUSDT":
        btc_15m = strip_tz(compute_indicators(await fetch("15m", limit=10000, sym="BTCUSDT")))
        btc_1h  = strip_tz(compute_indicators(await fetch("1h",  limit=10000, sym="BTCUSDT")))
    else:
        btc_15m = None
        btc_1h  = None

    # 레이블 붙이기
    labeled = await build_labeled_dataset(symbol)
    labeled["open_time"] = pd.to_datetime(labeled["open_time"])

    df_15m  = strip_tz(df_15m)
    df_1h   = strip_tz(df_1h)
    df_4h   = strip_tz(df_4h)
    labeled = strip_tz(labeled)

    df = add_multi_tf_features(df_15m, df_1h, df_4h)

    feat_cols = FEATURE_COLS if symbol != "BTCUSDT" else BTC_FEATURE_COLS
    if symbol != "BTCUSDT" and btc_15m is not None:
        df = add_btc_features(df, btc_15m, btc_1h)

    df = df.merge(labeled[["open_time", "label"]], on="open_time", how="inner")
    df = df.dropna(subset=feat_cols)

    X = df[feat_cols]
    y = df["label"].map({"BUY": 2, "HOLD": 1, "SELL": 0})

    print(f"[{symbol}] 학습 데이터: {len(X)}개, 피처: {len(feat_cols)}개")
    return X, y
