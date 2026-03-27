"""
백테스트 실행 스크립트
Usage: PYTHONPATH=. python3 scripts/run_backtest.py
"""
import asyncio
import sys
sys.path.insert(0, ".")

from backtest.engine import run_backtest
from config import settings


async def main():
    for symbol in settings.symbols:
        print(f"\n{'='*50}")
        print(f"  백테스트: {symbol}")
        print(f"{'='*50}")
        result = await run_backtest(symbol)

        print(f"초기 자본:    ${result['initial_capital']:,.2f}")
        print(f"최종 자본:    ${result['final_capital']:,.2f}")
        print(f"총 수익률:    {result['total_return']:+.2f}%")
        print(f"MDD:          -{result['mdd']:.2f}%")
        print(f"Sharpe:       {result['sharpe']:.3f}")
        print(f"트레이드 수:  {result['num_trades']}회")
        print(f"승률:         {result['win_rate']:.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
