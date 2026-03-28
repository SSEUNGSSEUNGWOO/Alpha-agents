"""
Paper Trader — 포트폴리오 기반 자본 관리
- 심볼별 독립 $1000 대신 단일 풀(TOTAL_CAPITAL)에서 동적 배분
- position_ratio × 현재 가용 현금 = 투자 금액
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

# ── 포트폴리오 상태 (메모리) ────────────────────────────────
_portfolio_cash: float = settings.total_capital   # 가용 현금
_positions: dict[str, dict] = {}                  # symbol → {qty, entry_price}


def get_portfolio_cash() -> float:
    return _portfolio_cash


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
    global _portfolio_cash

    if not state.get("approved"):
        return {**state, "order_id": None, "executed_price": None,
                "executed_qty": None, "error": None}

    symbol     = state["symbol"]
    action     = state["action"]
    confidence = state["confidence"]
    signals    = state.get("signals", {})
    pos_ratio  = state["position_ratio"]   # 총 자본 대비 비율 (e.g. 0.25)

    try:
        price = await _current_price(symbol)
        pos   = _positions.get(symbol)

        if action == "BUY" and pos is None:
            # 가용 현금의 pos_ratio만큼 투자 (최소 $10 이상일 때만)
            spend = _portfolio_cash * pos_ratio
            if spend < 10.0:
                return {**state, "order_id": None, "executed_price": None,
                        "executed_qty": None, "error": "insufficient cash"}

            buy_price = price * (1 + SLIPPAGE)
            fee       = spend * COMMISSION
            qty       = (spend - fee) / buy_price

            _portfolio_cash -= spend
            _positions[symbol] = {"qty": qty, "entry_price": buy_price, "invested": spend}

            await _record_trade(symbol, "BUY", buy_price, qty, fee, confidence, signals, None)
            log.info(
                f"[PAPER][{symbol}] BUY  {qty:.5f} @ ${buy_price:,.2f}"
                f"  spend=${spend:.2f}  cash_left=${_portfolio_cash:.2f}"
            )

            return {**state, "order_id": f"paper-buy-{int(datetime.now().timestamp())}",
                    "executed_price": buy_price, "executed_qty": qty, "error": None}

        elif action == "SELL" and pos is not None:
            sell_price = price * (1 - SLIPPAGE)
            proceeds   = pos["qty"] * sell_price
            fee        = proceeds * COMMISSION
            pnl        = (proceeds - fee) - pos["invested"]

            _portfolio_cash += proceeds - fee
            del _positions[symbol]

            await _record_trade(symbol, "SELL", sell_price, pos["qty"], fee, confidence, signals, pnl)
            pnl_sign = "+" if pnl >= 0 else ""
            log.info(
                f"[PAPER][{symbol}] SELL {pos['qty']:.5f} @ ${sell_price:,.2f}"
                f"  PnL={pnl_sign}{pnl:.2f}  cash=${_portfolio_cash:.2f}"
            )

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
        "portfolio_cash": round(_portfolio_cash, 2),
        "positions": {
            k: {
                "qty":         round(v["qty"], 8),
                "entry_price": round(v["entry_price"], 4),
                "invested":    round(v["invested"], 2),
            }
            for k, v in _positions.items()
        },
    }
