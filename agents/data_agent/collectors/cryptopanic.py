"""
CryptoPanic 뉴스 감성 수집기
- 무료 API 키 필요: https://cryptopanic.com/developers/api/ (무료 가입)
- .env에 CRYPTOPANIC_API_KEY=your_key 추가
- 지금부터 쌓아서 향후 학습 피처로 활용
"""
import asyncio
import logging
from datetime import datetime, timezone

import httpx
from storage import get_pool

log = logging.getLogger("cryptopanic-collector")

BASE_URL = "https://cryptopanic.com/api/v1/posts/"

SYMBOL_CURRENCIES = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "SOLUSDT": "SOL",
    "BNBUSDT": "BNB",
    "XRPUSDT": "XRP",
}


async def _ensure_table() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS news_sentiment (
                id          BIGSERIAL PRIMARY KEY,
                symbol      VARCHAR(20) NOT NULL,
                published_at TIMESTAMPTZ NOT NULL,
                title       TEXT,
                sentiment   VARCHAR(10),  -- positive / negative / neutral
                votes_pos   INT DEFAULT 0,
                votes_neg   INT DEFAULT 0,
                source      VARCHAR(50),
                UNIQUE (symbol, published_at, title)
            );
            CREATE INDEX IF NOT EXISTS idx_news_symbol_time
                ON news_sentiment (symbol, published_at DESC);
        """)


async def fetch_and_store(api_key: str) -> None:
    from config import settings
    await _ensure_table()
    pool = await get_pool()

    async with httpx.AsyncClient(timeout=15) as client:
        for symbol, currency in SYMBOL_CURRENCIES.items():
            if symbol not in settings.symbols:
                continue
            try:
                resp = await client.get(BASE_URL, params={
                    "auth_token": api_key,
                    "currencies": currency,
                    "public": "true",
                    "kind": "news",
                })
                resp.raise_for_status()
                posts = resp.json().get("results", [])

                rows = []
                for p in posts:
                    pub = datetime.fromisoformat(
                        p["published_at"].replace("Z", "+00:00")
                    )
                    votes = p.get("votes", {})
                    pos = votes.get("positive", 0) or 0
                    neg = votes.get("negative", 0) or 0
                    sentiment = "positive" if pos > neg else ("negative" if neg > pos else "neutral")
                    rows.append((
                        symbol,
                        pub,
                        p.get("title", "")[:500],
                        sentiment,
                        pos,
                        neg,
                        p.get("source", {}).get("domain", ""),
                    ))

                if rows:
                    async with pool.acquire() as conn:
                        await conn.executemany(
                            """
                            INSERT INTO news_sentiment
                                (symbol, published_at, title, sentiment, votes_pos, votes_neg, source)
                            VALUES ($1, $2, $3, $4, $5, $6, $7)
                            ON CONFLICT (symbol, published_at, title) DO NOTHING
                            """,
                            rows,
                        )
                    log.info(f"CryptoPanic 저장: {symbol} +{len(rows)}개")

                await asyncio.sleep(1)
            except Exception as e:
                log.error(f"CryptoPanic 오류 [{symbol}]: {e}")


async def run_cryptopanic_collector() -> None:
    """1시간마다 갱신"""
    import os
    api_key = os.environ.get("CRYPTOPANIC_API_KEY", "")
    if not api_key:
        log.warning("CRYPTOPANIC_API_KEY 없음 — 수집기 비활성화")
        return

    log.info("CryptoPanic 수집기 시작")
    while True:
        try:
            await fetch_and_store(api_key)
        except Exception as e:
            log.error(f"CryptoPanic 수집 오류: {e}")
        await asyncio.sleep(60 * 60)  # 1시간
