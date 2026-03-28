"""
Google Trends 주간 갱신 수집기
- 매주 일요일 최신 데이터 업데이트
"""
import asyncio
import logging
import time
from pytrends.request import TrendReq
from storage import get_pool

log = logging.getLogger("trends-collector")

TERMS = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "SOLUSDT": "solana",
    "BNBUSDT": "bnb",
    "XRPUSDT": "xrp",
}


async def fetch_and_store() -> None:
    from config import settings
    pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 30))
    pool = await get_pool()

    for symbol in settings.symbols:
        term = TERMS.get(symbol)
        if not term:
            continue
        try:
            pytrends.build_payload([term], timeframe="today 3-m", geo="")
            time.sleep(2)
            df = pytrends.interest_over_time()
            if df.empty:
                continue

            rows = [
                (symbol, row.Index.date(), float(row[term]))
                for row in df.itertuples()
                if not row.isPartial
            ]
            async with pool.acquire() as conn:
                await conn.executemany(
                    """
                    INSERT INTO onchain (symbol, date, metric, value)
                    VALUES ($1, $2, 'google_trends', $3)
                    ON CONFLICT (symbol, date, metric) DO UPDATE SET value = EXCLUDED.value
                    """,
                    rows,
                )
            log.info(f"Google Trends 갱신: {symbol} ({len(rows)}주)")
            time.sleep(5)
        except Exception as e:
            log.error(f"Google Trends 오류 [{symbol}]: {e}")


async def run_trends_collector() -> None:
    """7일마다 갱신"""
    while True:
        try:
            await fetch_and_store()
        except Exception as e:
            log.error(f"Trends collector 오류: {e}")
        await asyncio.sleep(60 * 60 * 24 * 7)  # 7일
