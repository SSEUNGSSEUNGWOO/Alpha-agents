import os
import asyncpg
from config import settings


_pool: asyncpg.Pool | None = None


def _get_dsn() -> str:
    # Railway는 DATABASE_URL을 직접 os.environ에 주입
    url = os.environ.get("DATABASE_URL") or settings.postgres_dsn
    # asyncpg는 postgres:// 또는 postgresql:// 모두 지원
    return url


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = _get_dsn()
        print(f"[DB] connecting to: {dsn[:30]}...", flush=True)
        _pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=2,
            max_size=10,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def init_tables() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                id          BIGSERIAL PRIMARY KEY,
                symbol      VARCHAR(20) NOT NULL,
                interval    VARCHAR(5)  NOT NULL,
                open_time   TIMESTAMPTZ NOT NULL,
                open        NUMERIC NOT NULL,
                high        NUMERIC NOT NULL,
                low         NUMERIC NOT NULL,
                close       NUMERIC NOT NULL,
                volume      NUMERIC NOT NULL,
                UNIQUE (symbol, interval, open_time)
            );

            CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_interval_time
                ON ohlcv (symbol, interval, open_time DESC);

            CREATE TABLE IF NOT EXISTS onchain (
                id          BIGSERIAL PRIMARY KEY,
                symbol      VARCHAR(20) NOT NULL,
                date        DATE NOT NULL,
                metric      VARCHAR(50) NOT NULL,
                value       NUMERIC,
                UNIQUE (symbol, date, metric)
            );

            CREATE TABLE IF NOT EXISTS trades (
                id              BIGSERIAL PRIMARY KEY,
                symbol          VARCHAR(20) NOT NULL,
                side            VARCHAR(5)  NOT NULL,
                price           NUMERIC NOT NULL,
                quantity        NUMERIC NOT NULL,
                fee             NUMERIC NOT NULL DEFAULT 0,
                order_id        VARCHAR(50),
                executed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                xgb_confidence  NUMERIC,
                features        JSONB,
                pnl             NUMERIC,
                mode            VARCHAR(10) NOT NULL DEFAULT 'paper'
            );

            ALTER TABLE trades ADD COLUMN IF NOT EXISTS pnl  NUMERIC;
            ALTER TABLE trades ADD COLUMN IF NOT EXISTS mode VARCHAR(10) NOT NULL DEFAULT 'paper';
        """)
