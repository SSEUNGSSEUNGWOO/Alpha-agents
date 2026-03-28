"""
현재 pkl 모델들을 DB에 업로드
- 최초 1회 실행으로 Railway DB에 모델 초기화
사용법: DATABASE_URL=... PYTHONPATH=. python scripts/upload_models_to_db.py
"""
import asyncio
import pickle
import sys
from pathlib import Path
from sklearn.metrics import f1_score, accuracy_score
import numpy as np

sys.path.insert(0, ".")
from storage import get_pool, init_tables
from config import settings


async def upload(symbol: str) -> None:
    path = Path(f"models/xgb_{symbol.lower()}.pkl")
    if not path.exists():
        print(f"[{symbol}] 파일 없음 — 스킵")
        return

    with open(path, "rb") as f:
        model_data = f.read()

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS models (
                id          BIGSERIAL PRIMARY KEY,
                symbol      VARCHAR(20) NOT NULL,
                trained_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                window_days INT,
                accuracy    NUMERIC,
                f1_macro    NUMERIC,
                model_data  BYTEA NOT NULL,
                UNIQUE (symbol, trained_at)
            );
            CREATE INDEX IF NOT EXISTS idx_models_symbol_time
                ON models (symbol, trained_at DESC);
            """,
        )
        await conn.execute(
            """
            INSERT INTO models (symbol, window_days, accuracy, f1_macro, model_data)
            VALUES ($1, 90, 0.5, 0.4, $2)
            ON CONFLICT DO NOTHING
            """,
            symbol, model_data,
        )
    print(f"[{symbol}] DB 업로드 완료")


async def main():
    await init_tables()
    for symbol in settings.symbols:
        await upload(symbol)


if __name__ == "__main__":
    asyncio.run(main())
