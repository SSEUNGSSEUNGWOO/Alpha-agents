"""
Execution Agent: Binance 주문 실행
- testnet 모드 지원
- MARKET 주문 (BUY/SELL)
- 잔고 조회 후 position_ratio 적용
"""
import asyncio
from binance import AsyncClient
from config import settings


async def _get_client() -> AsyncClient:
    return await AsyncClient.create(
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
        testnet=settings.binance_testnet,
    )


async def execute_order(state: dict) -> dict:
    if not state.get("approved"):
        return {**state, "order_id": None, "executed_price": None, "executed_qty": None, "error": None}

    action = state["action"]
    symbol = state["symbol"]
    position_ratio = state["position_ratio"]

    client = await _get_client()
    try:
        # USDT 잔고 조회
        account = await client.get_account()
        balances = {b["asset"]: float(b["free"]) for b in account["balances"]}
        usdt_free = balances.get("USDT", 0.0)

        # 현재가
        ticker = await client.get_symbol_ticker(symbol=symbol)
        price = float(ticker["price"])

        if action == "BUY":
            usdt_to_use = usdt_free * position_ratio
            qty = usdt_to_use / price
            qty = _floor_qty(qty, symbol)
            if qty <= 0:
                return {**state, "order_id": None, "executed_price": None, "executed_qty": None,
                        "error": "잔고 부족 또는 최소 수량 미달"}
            order = await client.create_order(
                symbol=symbol,
                side=AsyncClient.SIDE_BUY,
                type=AsyncClient.ORDER_TYPE_MARKET,
                quantity=qty,
            )

        elif action == "SELL":
            base_asset = symbol.replace("USDT", "")
            base_free = balances.get(base_asset, 0.0)
            qty = base_free * position_ratio
            qty = _floor_qty(qty, symbol)
            if qty <= 0:
                return {**state, "order_id": None, "executed_price": None, "executed_qty": None,
                        "error": f"{base_asset} 잔고 없음"}
            order = await client.create_order(
                symbol=symbol,
                side=AsyncClient.SIDE_SELL,
                type=AsyncClient.ORDER_TYPE_MARKET,
                quantity=qty,
            )

        fills = order.get("fills", [])
        avg_price = (
            sum(float(f["price"]) * float(f["qty"]) for f in fills) / sum(float(f["qty"]) for f in fills)
            if fills else price
        )
        executed_qty = float(order.get("executedQty", qty))

        return {
            **state,
            "order_id": str(order["orderId"]),
            "executed_price": avg_price,
            "executed_qty": executed_qty,
            "error": None,
        }

    except Exception as e:
        return {**state, "order_id": None, "executed_price": None, "executed_qty": None, "error": str(e)}
    finally:
        await client.close_connection()


def _floor_qty(qty: float, symbol: str) -> float:
    """심볼별 수량 정밀도 (간단 근사 — 실제론 exchange info 조회 필요)"""
    precision = {"BTCUSDT": 5, "ETHUSDT": 4}.get(symbol, 3)
    factor = 10 ** precision
    return int(qty * factor) / factor
