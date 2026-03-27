"""
Paper Trader
- Binance testnet에서 현재가를 조회하되 실제 주문은 내지 않음
- 가상 포지션/잔고를 메모리에 유지하고 DB trades 테이블에 기록
- 수수료/슬리피지 시뮬레이션 포함
"""
import logging
from datetime import datetime, timezone
from binance import AsyncClient
from config import settings
from storage import get_pool

log = logging.getLogger("paper-trader")

COMMISSION = 0.0004
SLIPPAGE   = 0.0002

# 심볼별 가상 포지션 상태 (메모리)
_positions: dict[str, dict] = {}  # symbol → {qty, entry_price}
_cash: dict[str, float] = {}      # symbol → usdt


def _get_cash(symbol: str) -> float:
    if symbol not in _cash:
        _cash[symbol] = 1000.0  # 심볼당 $1000 초기 자본
    return _cash[symbol]


async def _current_price(symbol: str) -> float:
    client = await AsyncClient.create(
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
        testnet=settings.binance_testnet,
    )
    try:
        ticker = await client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])
    finally:
        await client.close_connection()


async def _record_trade(
    symbol: str, side: str, price: float, qty: float,
    fee: float, confidence: float, signals: dict, pnl: float | None
) -> None:
    pool = await get_pool()
    import json
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO trades
                (symbol, side, price, quantity, fee, xgb_confidence, features, pnl, mode)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'paper')
            """,
            symbol, side, price, qty, fee, confidence,
            json.dumps({k: round(float(v), 6) for k, v in signals.items()}),
            pnl,
        )


async def execute_paper(state: dict) -> dict:
    if not state.get("approved"):
        return {**state, "order_id": None, "executed_price": None,
                "executed_qty": None, "error": None}

    symbol     = state["symbol"]
    action     = state["action"]
    confidence = state["confidence"]
    signals    = state.get("signals", {})
    pos_ratio  = state["position_ratio"]

    try:
        price = await _current_price(symbol)
        pos   = _positions.get(symbol)

        if action == "BUY" and pos is None:
            cash       = _get_cash(symbol)
            spend      = cash * pos_ratio
            buy_price  = price * (1 + SLIPPAGE)
            fee        = spend * COMMISSION
            qty        = (spend - fee) / buy_price
            _cash[symbol] = cash - spend
            _positions[symbol] = {"qty": qty, "entry_price": buy_price}

            await _record_trade(symbol, "BUY", buy_price, qty, fee, confidence, signals, None)
            log.info(f"[PAPER][{symbol}] BUY  {qty:.5f} @ ${buy_price:,.2f}  (conf={confidence:.2f})")

            return {**state, "order_id": f"paper-buy-{int(datetime.now().timestamp())}",
                    "executed_price": buy_price, "executed_qty": qty, "error": None}

        elif action == "SELL" and pos is not None:
            sell_price = price * (1 - SLIPPAGE)
            proceeds   = pos["qty"] * sell_price
            fee        = proceeds * COMMISSION
            pnl        = (proceeds - fee) - (pos["qty"] * pos["entry_price"])
            _cash[symbol] = _get_cash(symbol) + proceeds - fee
            del _positions[symbol]

            await _record_trade(symbol, "SELL", sell_price, pos["qty"], fee, confidence, signals, pnl)
            pnl_sign = "+" if pnl >= 0 else ""
            log.info(f"[PAPER][{symbol}] SELL {pos['qty']:.5f} @ ${sell_price:,.2f}  PnL={pnl_sign}{pnl:.2f}")

            return {**state, "order_id": f"paper-sell-{int(datetime.now().timestamp())}",
                    "executed_price": sell_price, "executed_qty": pos["qty"], "error": None}

        else:
            return {**state, "order_id": None, "executed_price": None,
                    "executed_qty": None, "error": None}

    except Exception as e:
        log.exception(f"[PAPER][{symbol}] 오류: {e}")
        return {**state, "order_id": None, "executed_price": None,
                "executed_qty": None, "error": str(e)}


def get_paper_status() -> dict:
    """현재 paper trading 상태 반환"""
    return {
        "cash":      {k: round(v, 2) for k, v in _cash.items()},
        "positions": {k: {**v, "entry_price": round(v["entry_price"], 2)}
                      for k, v in _positions.items()},
    }
