import pandas as pd
import numpy as np
from storage import get_pool


HORIZON = 32        # 32봉 × 15m = 8시간 후
THRESHOLD = 0.01    # ±1% 기준


async def build_labeled_dataset(symbol: str) -> pd.DataFrame:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT open_time, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol = $1 AND interval = '15m'
            ORDER BY open_time ASC
            """,
            symbol,
        )

    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    # 미래 수익률 계산 (8시간 후)
    df["future_return"] = df["close"].shift(-HORIZON) / df["close"] - 1

    # 레이블 생성
    df["label"] = "HOLD"
    df.loc[df["future_return"] >  THRESHOLD, "label"] = "BUY"
    df.loc[df["future_return"] < -THRESHOLD, "label"] = "SELL"

    # 미래 데이터 없는 마지막 행 제거
    df = df.dropna(subset=["future_return"]).copy()

    label_counts = df["label"].value_counts()
    print(f"[{symbol}] 레이블 분포: {label_counts.to_dict()}")

    return df
