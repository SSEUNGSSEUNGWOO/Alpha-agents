"""
Alpha Agents 메인 실행 루프
- 15분마다 각 심볼에 대해 analysis→strategy→risk→execute 파이프라인 실행
- OHLCV 수집기 동시 실행
"""
import asyncio
import logging
from datetime import datetime

from config import settings
from storage import init_db
from graph.graph import get_graph
from agents.data_agent.collectors.ohlcv import run_collector as run_ohlcv_collector
from agents.data_agent.collectors.fear_greed import run_fear_greed_collector
from agents.data_agent.collectors.trends import run_trends_collector
from agents.data_agent.collectors.cryptopanic import run_cryptopanic_collector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("alpha-agents")

CYCLE_SECONDS = 60 * 15  # 15분


async def run_trading_cycle():
    graph = get_graph()
    for symbol in settings.symbols:
        print(f"▶ 사이클 시작: {symbol}", flush=True)
        log.info(f"▶ 사이클 시작: {symbol}")
        try:
            initial_state = {
                "symbol":         symbol,
                "signals":        {},
                "action":         "HOLD",
                "confidence":     0.0,
                "proba":          {},
                "approved":       False,
                "risk_reason":    "",
                "position_ratio": 0.0,
                "order_id":       None,
                "executed_price": None,
                "executed_qty":   None,
                "error":          None,
            }
            result = await asyncio.wait_for(graph.ainvoke(initial_state), timeout=60.0)

            msg = (
                f"[{symbol}] action={result['action']} conf={result['confidence']:.2f} "
                f"approved={result['approved']} reason='{result['risk_reason']}'"
            )
            print(msg, flush=True)
            log.info(msg)

            if result.get("order_id"):
                log.info(
                    f"[{symbol}] 주문 체결 — id={result['order_id']} "
                    f"price={result['executed_price']} qty={result['executed_qty']}"
                )
            if result.get("error"):
                log.warning(f"[{symbol}] 실행 오류: {result['error']}")

        except asyncio.TimeoutError:
            log.error(f"[{symbol}] 사이클 타임아웃 (60초 초과)")
        except Exception as e:
            log.exception(f"[{symbol}] 사이클 오류: {e}")


async def main():
    log.info("Alpha Agents 시작")
    await init_db()

    # 수집기 백그라운드 실행
    asyncio.create_task(run_ohlcv_collector())
    asyncio.create_task(run_fear_greed_collector())
    asyncio.create_task(run_trends_collector())
    asyncio.create_task(run_cryptopanic_collector())

    # 첫 사이클 즉시 실행 후 15분마다 반복
    while True:
        start = asyncio.get_event_loop().time()
        await run_trading_cycle()
        elapsed = asyncio.get_event_loop().time() - start
        sleep_for = max(0, CYCLE_SECONDS - elapsed)
        log.info(f"다음 사이클까지 {sleep_for/60:.1f}분 대기")
        await asyncio.sleep(sleep_for)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    asyncio.run(main())
