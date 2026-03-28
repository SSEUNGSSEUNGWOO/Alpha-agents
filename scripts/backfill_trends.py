"""
Google Trends 히스토리컬 백필
- pytrends로 최대 5년치 주별 검색량 가져오기
- onchain 테이블에 metric='google_trends'로 저장
사용법: PYTHONPATH=. python scripts/backfill_trends.py
"""
import asyncio
import sys
import time
from datetime import date, timedelta

import structlog
from pytrends.request import TrendReq

sys.path.insert(0, ".")
from storage import get_pool, init_tables

log = structlog.get_logger()

TERMS = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "SOLUSDT": "solana",
    "BNBUSDT": "bnb",
    "XRPUSDT": "xrp",
}


async def save_trends(symbol: str, rows: list[tuple]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO onchain (symbol, date, metric, value)
            VALUES ($1, $2, 'google_trends', $3)
            ON CONFLICT (symbol, date, metric) DO UPDATE SET value = EXCLUDED.value
            """,
            rows,
        )


async def backfill_symbol(symbol: str, term: str) -> None:
    log.info("trends.fetch", symbol=symbol, term=term)
    pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 30))

    # pytrends는 한 번에 최대 5년 (weekly)
    pytrends.build_payload([term], timeframe="today 5-y", geo="")
    time.sleep(2)  # rate limit 방지

    df = pytrends.interest_over_time()
    if df.empty:
        log.warning("trends.empty", symbol=symbol)
        return

    rows = [
        (symbol, idx.date(), float(val))
        for idx, val, partial in zip(df.index, df[term], df["isPartial"])
        if not partial
    ]
    await save_trends(symbol, rows)
    log.info("trends.saved", symbol=symbol, count=len(rows))


async def main() -> None:
    await init_tables()
    from config import settings
    for symbol in settings.symbols:
        term = TERMS.get(symbol)
        if not term:
            continue
        await backfill_symbol(symbol, term)
        time.sleep(5)  # 심볼 간 딜레이 (rate limit)


if __name__ == "__main__":
    asyncio.run(main())
