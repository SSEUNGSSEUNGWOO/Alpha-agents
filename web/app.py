"""
Alpha Agents 웹 대시보드
Usage: PYTHONPATH=. python3 web/app.py
"""
import sys
sys.path.insert(0, ".")

import asyncio
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

from storage import init_db, get_pool
from agents.analysis_agent.technical import fetch_ohlcv

app = FastAPI()


async def get_dashboard_data() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        symbols = await conn.fetch(
            "SELECT DISTINCT symbol FROM trades WHERE mode='paper'"
        )

        if not symbols:
            return {"symbols": [], "total_pnl": 0.0}

        result = []
        total_realized = 0.0
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

            buys   = [t for t in trades if t["side"] == "BUY"]
            sells  = [t for t in trades if t["side"] == "SELL"]
            realized = sum(float(t["pnl"]) for t in trades if t["pnl"] is not None)
            total_realized += realized

            open_pos = len(buys) > len(sells)
            unrealized = 0.0
            entry_price = None
            qty = None
            current = None

            if open_pos:
                df = await fetch_ohlcv(sym, "15m", limit=1)
                current = float(df["close"].iloc[-1])
                entry_price = float(buys[-1]["price"])
                qty = float(buys[-1]["quantity"])
                unrealized = (current - entry_price) * qty
                total_unrealized += unrealized

            # 최근 5개 거래
            recent = []
            for t in reversed(trades[-5:]):
                recent.append({
                    "side":  t["side"],
                    "price": float(t["price"]),
                    "qty":   float(t["quantity"]),
                    "pnl":   float(t["pnl"]) if t["pnl"] else None,
                    "time":  t["executed_at"].strftime("%m/%d %H:%M"),
                })

            pnls = [float(t["pnl"]) for t in trades if t["pnl"] is not None]
            win_rate = round(100 * sum(1 for p in pnls if p > 0) / len(pnls), 1) if pnls else 0

            result.append({
                "symbol":      sym,
                "buys":        len(buys),
                "sells":       len(sells),
                "realized":    round(realized, 2),
                "unrealized":  round(unrealized, 2),
                "win_rate":    win_rate,
                "open":        open_pos,
                "entry_price": entry_price,
                "current":     current,
                "qty":         qty,
                "recent":      recent,
            })

        return {
            "symbols":      result,
            "total_pnl":    round(total_realized + total_unrealized, 2),
            "total_real":   round(total_realized, 2),
            "total_unreal": round(total_unrealized, 2),
        }


@app.on_event("startup")
async def startup():
    await init_db()


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    data = await get_dashboard_data()
    return render_html(data)


@app.get("/api/status")
async def api_status():
    return await get_dashboard_data()


def render_html(data: dict) -> str:
    total_color = "profit" if data["total_pnl"] >= 0 else "loss"

    if not data["symbols"]:
        body = """
        <div class="card center">
            <div class="label">거래 없음</div>
            <div style="color:#888; margin-top:8px">HOLD 신호 또는 서킷브레이커 작동 중</div>
        </div>
        """
    else:
        cards = ""
        for s in data["symbols"]:
            pnl_color = "profit" if s["realized"] >= 0 else "loss"
            unreal_color = "profit" if s["unrealized"] >= 0 else "loss"
            pos_html = ""
            if s["open"]:
                pos_html = f"""
                <div class="row">
                    <span class="label">보유</span>
                    <span>{s['qty']:.5f} @ ${s['entry_price']:,.2f}</span>
                </div>
                <div class="row">
                    <span class="label">현재가</span>
                    <span>${s['current']:,.2f}</span>
                </div>
                <div class="row">
                    <span class="label">미실현</span>
                    <span class="{unreal_color}">${s['unrealized']:+.2f}</span>
                </div>
                """
            else:
                pos_html = '<div class="row"><span class="label">포지션</span><span style="color:#888">없음</span></div>'

            recent_rows = ""
            for t in s["recent"]:
                side_class = "buy" if t["side"] == "BUY" else "sell"
                pnl_str = f'<span class="{"profit" if t["pnl"] and t["pnl"]>0 else "loss"}">${t["pnl"]:+.2f}</span>' if t["pnl"] is not None else ""
                recent_rows += f"""
                <tr>
                    <td class="{side_class}">{t['side']}</td>
                    <td>${t['price']:,.2f}</td>
                    <td>{t['qty']:.5f}</td>
                    <td>{pnl_str}</td>
                    <td style="color:#888">{t['time']}</td>
                </tr>
                """

            cards += f"""
            <div class="card">
                <div class="symbol">{s['symbol']}</div>
                <div class="row">
                    <span class="label">거래</span>
                    <span>BUY {s['buys']}회 / SELL {s['sells']}회</span>
                </div>
                <div class="row">
                    <span class="label">실현손익</span>
                    <span class="{pnl_color}">${s['realized']:+.2f}</span>
                </div>
                {pos_html}
                <div class="row">
                    <span class="label">승률</span>
                    <span>{s['win_rate']}%</span>
                </div>
                {"<table class='trade-table'><tr><th>방향</th><th>가격</th><th>수량</th><th>PnL</th><th>시간</th></tr>" + recent_rows + "</table>" if recent_rows else ""}
            </div>
            """
        body = cards

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="60">
<title>Alpha Agents</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f0f0f; color: #e0e0e0; font-family: -apple-system, sans-serif; padding: 16px; }}
  h1 {{ font-size: 18px; color: #fff; margin-bottom: 4px; }}
  .subtitle {{ color: #666; font-size: 12px; margin-bottom: 16px; }}
  .total {{ background: #1a1a1a; border-radius: 12px; padding: 16px; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }}
  .total-label {{ color: #888; font-size: 13px; }}
  .total-value {{ font-size: 28px; font-weight: bold; }}
  .profit {{ color: #00c896; }}
  .loss {{ color: #ff4d4d; }}
  .card {{ background: #1a1a1a; border-radius: 12px; padding: 16px; margin-bottom: 12px; }}
  .card.center {{ text-align: center; padding: 32px; }}
  .symbol {{ font-size: 16px; font-weight: bold; color: #fff; margin-bottom: 12px; }}
  .row {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #2a2a2a; font-size: 14px; }}
  .label {{ color: #888; }}
  .trade-table {{ width: 100%; margin-top: 12px; font-size: 12px; border-collapse: collapse; }}
  .trade-table th {{ color: #666; text-align: left; padding: 4px; }}
  .trade-table td {{ padding: 4px; border-top: 1px solid #2a2a2a; }}
  .buy {{ color: #00c896; font-weight: bold; }}
  .sell {{ color: #ff4d4d; font-weight: bold; }}
</style>
</head>
<body>
<h1>Alpha Agents</h1>
<div class="subtitle">Paper Trading · 60초마다 자동 갱신</div>

<div class="total">
  <div>
    <div class="total-label">총 손익</div>
    <div class="total-value {total_color}">${data['total_pnl']:+.2f}</div>
  </div>
  <div style="text-align:right">
    <div style="color:#888; font-size:12px">실현 ${data['total_real']:+.2f}</div>
    <div style="color:#888; font-size:12px">미실현 ${data['total_unreal']:+.2f}</div>
  </div>
</div>

{body}
</body>
</html>"""


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
