"""
Paper trading 성과 조회
Usage: PYTHONPATH=. python3 scripts/paper_status.py
"""
import asyncio
import sys
sys.path.insert(0, ".")

from storage import get_pool, init_db


async def main():
    await init_db()
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 심볼별 요약
        rows = await conn.fetch("""
            SELECT
                symbol,
                COUNT(*) FILTER (WHERE side='BUY')  AS buys,
                COUNT(*) FILTER (WHERE side='SELL') AS sells,
                ROUND(SUM(pnl) FILTER (WHERE side='SELL'), 2) AS total_pnl,
                ROUND(AVG(pnl) FILTER (WHERE side='SELL'), 2) AS avg_pnl,
                ROUND(
                    100.0 * COUNT(*) FILTER (WHERE side='SELL' AND pnl > 0)
                    / NULLIF(COUNT(*) FILTER (WHERE side='SELL'), 0)
                , 1) AS win_rate
            FROM trades
            WHERE mode = 'paper'
            GROUP BY symbol
            ORDER BY symbol
        """)

        # 최근 10개 거래
        recent = await conn.fetch("""
            SELECT symbol, side, price, quantity, pnl, executed_at
            FROM trades
            WHERE mode = 'paper'
            ORDER BY executed_at DESC
            LIMIT 10
        """)

    print("\n=== Paper Trading 성과 ===")
    if not rows:
        print("아직 체결된 거래 없음")
    for r in rows:
        print(f"\n[{r['symbol']}]")
        print(f"  BUY {r['buys']}회 / SELL {r['sells']}회")
        print(f"  총 PnL:  ${r['total_pnl'] or 0:+.2f}")
        print(f"  평균 PnL: ${r['avg_pnl'] or 0:+.2f}")
        print(f"  승률:    {r['win_rate'] or 0:.1f}%")

    print("\n=== 최근 거래 ===")
    for t in recent:
        pnl_str = f"  PnL=${float(t['pnl']):+.2f}" if t["pnl"] is not None else ""
        print(f"  {t['executed_at'].strftime('%m/%d %H:%M')}  {t['symbol']} {t['side']}"
              f"  ${float(t['price']):,.2f} × {float(t['quantity']):.5f}{pnl_str}")


if __name__ == "__main__":
    asyncio.run(main())
