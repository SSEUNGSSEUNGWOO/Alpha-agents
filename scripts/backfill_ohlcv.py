"""
Binance 히스토리컬 OHLCV 백필 스크립트
사용법: python scripts/backfill_ohlcv.py --days 90
"""
import asyncio
import argparse
from datetime import datetime, timedelta
import structlog
from binance import AsyncClient
from config import settings
from storage import get_pool, init_tables

log = structlog.get_logger()

INTERVALS = ["15m", "1h", "4h"]
LIMIT_PER_REQUEST = 1000  # Binance 최대


async def backfill(client: AsyncClient, symbol: str, interval: str, days: int) -> None:
    pool = await get_pool()
    start_ts = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    total = 0

    while True:
        klines = await client.get_klines(
            symbol=symbol,
            interval=interval,
            startTime=start_ts,
            limit=LIMIT_PER_REQUEST,
        )
        if not klines:
            break

        rows = [
            (
                symbol,
                interval,
                datetime.utcfromtimestamp(k[0] / 1000),
                float(k[1]),
                float(k[2]),
                float(k[3]),
                float(k[4]),
                float(k[5]),
            )
            for k in klines
        ]

        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO ohlcv (symbol, interval, open_time, open, high, low, close, volume)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (symbol, interval, open_time) DO NOTHING
                """,
                rows,
            )

        total += len(rows)
        start_ts = klines[-1][6] + 1  # 마지막 봉 close_time + 1ms

        if len(klines) < LIMIT_PER_REQUEST:
            break

        await asyncio.sleep(0.2)  # Rate limit 방지

    log.info("backfill.done", symbol=symbol, interval=interval, total=total)


async def main(days: int, extra_symbols: list = None) -> None:
    await init_tables()
    client = await AsyncClient.create(
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
    )
    symbols = extra_symbols if extra_symbols else settings.symbols
    try:
        for symbol in symbols:
            for interval in INTERVALS:
                log.info("backfill.start", symbol=symbol, interval=interval, days=days)
                await backfill(client, symbol, interval, days)
    finally:
        await client.close_connection()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--symbols", type=str, default="")
    args = parser.parse_args()
    extra = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else []
    asyncio.run(main(args.days, extra))
