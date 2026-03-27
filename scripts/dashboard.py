"""
Paper Trading 대시보드
Usage: PYTHONPATH=. python3 scripts/dashboard.py
"""
import asyncio
import sys
sys.path.insert(0, ".")

from storage import get_pool, init_db
from agents.analysis_agent.technical import fetch_ohlcv


async def current_price(symbol: str) -> float:
    df = await fetch_ohlcv(symbol, "15m", limit=1)
    return float(df["close"].iloc[-1])


async def main():
    await init_db()
    pool = await get_pool()

    print("\n" + "="*50)
    print("       Alpha Agents — Paper Trading 현황")
    print("="*50)

    async with pool.acquire() as conn:
        # 심볼별 포지션 재구성 (마지막 미청산 BUY)
        symbols = await conn.fetch(
            "SELECT DISTINCT symbol FROM trades WHERE mode='paper'"
        )

        if not symbols:
            print("\n  아직 체결된 거래 없음 (HOLD 중)")
            print("\n  신호 대기 중... (MDD 서킷브레이커 또는 confidence 미달)")
            print("="*50)
            return

        total_realized   = 0.0
        total_unrealized = 0.0

        for row in symbols:
            sym = row["symbol"]
            trades = await conn.fetch(
                """
                SELECT side, price, quantity, pnl, executed_at
                FROM trades WHERE symbol=$1 AND mode='paper'
                ORDER BY executed_at ASC
                """, sym
            )

            realized = sum(float(t["pnl"]) for t in trades if t["pnl"] is not None)
            total_realized += realized

            # 미청산 포지션 확인
            buys  = [t for t in trades if t["side"] == "BUY"]
            sells = [t for t in trades if t["side"] == "SELL"]
            open_position = len(buys) > len(sells)

            print(f"\n  [{sym}]")
            print(f"  거래 횟수:   BUY {len(buys)}회 / SELL {len(sells)}회")
            print(f"  실현 손익:   ${realized:+.2f}")

            if open_position:
                entry = buys[-1]
                price = await current_price(sym)
                unreal = (price - float(entry["price"])) * float(entry["quantity"])
                total_unrealized += unreal
                print(f"  보유 중:     {float(entry['quantity']):.5f} @ ${float(entry['price']):,.2f}")
                print(f"  현재가:      ${price:,.2f}")
                print(f"  미실현 손익: ${unreal:+.2f}")
            else:
                print(f"  포지션:      없음")

        print(f"\n{'─'*50}")
        print(f"  실현 손익 합계:   ${total_realized:+.2f}")
        print(f"  미실현 손익 합계: ${total_unrealized:+.2f}")
        print(f"  총 손익:          ${total_realized + total_unrealized:+.2f}")
        print("="*50 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
