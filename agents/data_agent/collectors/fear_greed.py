"""
Fear & Greed Index 수집기
- alternative.me 무료 API (키 불필요)
- 하루 1회 갱신 → onchain 테이블에 저장
"""
import asyncio
import httpx
import logging
from datetime import datetime, timezone

from storage import get_pool

log = logging.getLogger("fear-greed")
API_URL = "https://api.alternative.me/fng/?limit=365"


async def fetch_and_store() -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(API_URL)
        resp.raise_for_status()
        data = resp.json()["data"]

    pool = await get_pool()
    async with pool.acquire() as conn:
        for item in data:
            ts   = datetime.fromtimestamp(int(item["timestamp"]), tz=timezone.utc).date()
            val  = float(item["value"])
            await conn.execute(
                """
                INSERT INTO onchain (symbol, date, metric, value)
                VALUES ('BTC', $1, 'fear_greed', $2)
                ON CONFLICT (symbol, date, metric) DO UPDATE SET value = EXCLUDED.value
                """,
                ts, val,
            )

    log.info(f"Fear & Greed 저장 완료: 최신 {data[0]['value']} ({data[0]['value_classification']})")


async def run_fear_greed_collector() -> None:
    """24시간마다 갱신"""
    while True:
        try:
            await fetch_and_store()
        except Exception as e:
            log.error(f"Fear & Greed 수집 오류: {e}")
        await asyncio.sleep(60 * 60 * 24)  # 24시간
