from typing import TypedDict, Optional


class AgentState(TypedDict):
    symbol: str

    # Analysis Agent 결과
    signals: dict                   # 기술적 지표 dict

    # Strategy Agent 결과
    action: str                     # "BUY" | "SELL" | "HOLD"
    confidence: float
    proba: dict                     # {"BUY": 0.x, "SELL": 0.x, "HOLD": 0.x}

    # Risk Agent 결과
    approved: bool                  # 진입 승인 여부
    risk_reason: str                # 거부 사유 (approved=False일 때)
    position_ratio: float           # 실제 진입 비중 (0.0 ~ max_position_ratio)

    # Execution Agent 결과
    order_id: Optional[str]
    executed_price: Optional[float]
    executed_qty: Optional[float]
    error: Optional[str]
