"""
Risk Agent: 포지션 진입 전 리스크 체크
- confidence threshold
- MDD circuit breaker
- 포지션 비중 결정
"""
from config import settings
from storage import get_pool

CONFIDENCE_THRESHOLD = 0.40   # 최소 신뢰도
MDD_QUERY = """
    SELECT
        MIN(close) AS min_close,
        MAX(close) AS max_close
    FROM ohlcv
    WHERE symbol = $1 AND interval = '1h'
      AND open_time >= NOW() - INTERVAL '30 days'
"""


async def _current_mdd(symbol: str) -> float:
    """최근 30일 고점 대비 낙폭(MDD) 근사치"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(MDD_QUERY, symbol)
    if not row or row["max_close"] is None or row["max_close"] == 0:
        return 0.0
    return (row["max_close"] - row["min_close"]) / row["max_close"]


async def check_risk(state: dict) -> dict:
    action = state["action"]
    confidence = state["confidence"]
    symbol = state["symbol"]

    # HOLD면 바로 통과 (실행 없음)
    if action == "HOLD":
        return {**state, "approved": False, "risk_reason": "HOLD — no trade", "position_ratio": 0.0}

    # 신뢰도 체크
    if confidence < CONFIDENCE_THRESHOLD:
        return {
            **state,
            "approved": False,
            "risk_reason": f"confidence {confidence:.2f} < threshold {CONFIDENCE_THRESHOLD}",
            "position_ratio": 0.0,
        }

    # MDD 서킷브레이커
    mdd = await _current_mdd(symbol)
    if mdd > settings.mdd_circuit_breaker:
        return {
            **state,
            "approved": False,
            "risk_reason": f"MDD {mdd:.1%} > circuit breaker {settings.mdd_circuit_breaker:.1%}",
            "position_ratio": 0.0,
        }

    # 신뢰도에 비례한 포지션 비중 (0.5 conf → 50% of max, 1.0 conf → 100% of max)
    ratio = min(settings.max_position_ratio * (confidence / 1.0), settings.max_position_ratio)

    return {
        **state,
        "approved": True,
        "risk_reason": "",
        "position_ratio": round(ratio, 4),
    }
