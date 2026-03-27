import asyncio
from datetime import datetime
import structlog
from binance import AsyncClient
from config import settings
from storage import get_pool

log = structlog.get_logger()

INTERVALS = ["15m", "1h", "4h"]


async def fetch_and_store(client: AsyncClient, symbol: str, interval: str) -> None:
    klines = await client.get_klines(symbol=symbol, interval=interval, limit=100)
    pool = await get_pool()

    rows = [
        (
            symbol,
            interval,
            datetime.utcfromtimestamp(k[0] / 1000),
            float(k[1]),  # open
            float(k[2]),  # high
            float(k[3]),  # low
            float(k[4]),  # close
            float(k[5]),  # volume
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

    log.info("ohlcv.stored", symbol=symbol, interval=interval, count=len(rows))


async def run_collector() -> None:
    client = await AsyncClient.create(
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
        testnet=settings.binance_testnet,
    )
    try:
        while True:
            tasks = [
                fetch_and_store(client, symbol, interval)
                for symbol in settings.symbols
                for interval in INTERVALS
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(60)  # 1분마다 갱신
    finally:
        await client.close_connection()
