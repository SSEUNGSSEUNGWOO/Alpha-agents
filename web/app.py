"""
Alpha Agents 웹 대시보드
Usage: PYTHONPATH=. python3 web/app.py
"""
import sys
sys.path.insert(0, ".")

import asyncio
import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from storage import init_db, get_pool
from agents.analysis_agent.technical import fetch_ohlcv

app = FastAPI()

from config import settings as _settings
INITIAL_CAPITAL = _settings.total_capital  # 포트폴리오 총 자본


async def get_fear_greed() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value, date FROM onchain WHERE symbol='BTC' AND metric='fear_greed' ORDER BY date DESC LIMIT 1"
        )
    if not row:
        return {"value": 50, "label": "Neutral", "color": "#888"}
    val = int(row["value"])
    if val <= 25:
        label, color = "Extreme Fear", "#ff4d4d"
    elif val <= 45:
        label, color = "Fear", "#ff9944"
    elif val <= 55:
        label, color = "Neutral", "#888"
    elif val <= 75:
        label, color = "Greed", "#88cc44"
    else:
        label, color = "Extreme Greed", "#00c896"
    return {"value": val, "label": label, "color": color}


async def get_prices() -> dict:
    """현재가 조회"""
    prices = {}
    from config import settings
    for sym in settings.symbols:
        try:
            df = await fetch_ohlcv(sym, "15m", limit=2)
            if len(df) >= 2:
                prices[sym] = {
                    "price": float(df["close"].iloc[-1]),
                    "change": float((df["close"].iloc[-1] - df["close"].iloc[-2]) / df["close"].iloc[-2] * 100),
                }
            elif len(df) == 1:
                prices[sym] = {"price": float(df["close"].iloc[-1]), "change": 0.0}
        except Exception:
            prices[sym] = {"price": 0.0, "change": 0.0}
    return prices


async def get_dashboard_data() -> dict:
    pool = await get_pool()
    prices = await get_prices()
    fg = await get_fear_greed()

    async with pool.acquire() as conn:
        from config import settings
        all_symbols = settings.symbols

        result = []
        total_realized  = 0.0
        total_unrealized = 0.0
        total_invested  = 0.0   # 현재 열린 포지션에 묶인 금액

        for sym in all_symbols:
            trades = await conn.fetch(
                """
                SELECT side, price, quantity, fee, pnl, executed_at
                FROM trades WHERE symbol=$1 AND mode='paper'
                ORDER BY executed_at ASC
                """, sym
            )

            buys     = [t for t in trades if t["side"] == "BUY"]
            sells    = [t for t in trades if t["side"] == "SELL"]
            realized = sum(float(t["pnl"]) for t in trades if t["pnl"] is not None)
            total_realized += realized

            open_pos    = len(buys) > len(sells)
            unrealized  = 0.0
            entry_price = None
            qty         = None
            invested    = 0.0
            current     = prices.get(sym, {}).get("price")

            if open_pos and current and buys:
                last_buy    = buys[-1]
                entry_price = float(last_buy["price"])
                qty         = float(last_buy["quantity"])
                # 실제 투자 금액 = qty * entry_price + fee
                fee_buy     = float(last_buy["fee"]) if last_buy["fee"] else 0.0
                invested    = qty * entry_price + fee_buy
                unrealized  = (current - entry_price) * qty
                total_unrealized += unrealized
                total_invested   += invested

            recent = []
            for t in reversed(trades[-5:]):
                recent.append({
                    "side":  t["side"],
                    "price": float(t["price"]),
                    "qty":   float(t["quantity"]),
                    "pnl":   float(t["pnl"]) if t["pnl"] else None,
                    "time":  t["executed_at"].strftime("%m/%d %H:%M"),
                })

            pnls     = [float(t["pnl"]) for t in trades if t["pnl"] is not None]
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
                "change":      prices.get(sym, {}).get("change", 0.0),
                "qty":         qty,
                "invested":    round(invested, 2),
                "recent":      recent,
            })

        # 포트폴리오 총 잔고 = 초기자본 + 실현손익 - 투자중 금액 + 현재 포지션 가치
        open_value   = sum(
            s["qty"] * s["current"]
            for s in result
            if s["open"] and s["qty"] and s["current"]
        )
        portfolio_cash   = INITIAL_CAPITAL + total_realized - total_invested
        total_balance    = portfolio_cash + open_value

        return {
            "symbols":         result,
            "total_pnl":       round(total_realized + total_unrealized, 2),
            "total_real":      round(total_realized, 2),
            "total_unreal":    round(total_unrealized, 2),
            "total_balance":   round(total_balance, 2),
            "portfolio_cash":  round(portfolio_cash, 2),
            "initial_capital": round(INITIAL_CAPITAL, 2),
            "fear_greed":      fg,
        }


@app.on_event("startup")
async def startup():
    await init_db()


@app.get("/api/ohlcv/{symbol}")
async def api_ohlcv(symbol: str, limit: int = 100):
    """캔들 데이터 API — 차트용"""
    df = await fetch_ohlcv(symbol.upper(), "15m", limit=limit)
    candles = []
    for _, row in df.iterrows():
        ts = int(row["open_time"].timestamp())
        candles.append({
            "time":  ts,
            "open":  float(row["open"]),
            "high":  float(row["high"]),
            "low":   float(row["low"]),
            "close": float(row["close"]),
        })
    return JSONResponse(candles)


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    try:
        data = await get_dashboard_data()
        return render_html(data)
    except Exception as e:
        import traceback
        return HTMLResponse(f"<pre>Error:\n{traceback.format_exc()}</pre>", status_code=500)


@app.get("/api/status")
async def api_status():
    return await get_dashboard_data()


def render_html(data: dict) -> str:
    total_color  = "profit" if data["total_pnl"] >= 0 else "loss"
    return_pct   = ((data["total_balance"] - data["initial_capital"]) / data["initial_capital"] * 100) if data["initial_capital"] else 0
    return_color = "profit" if return_pct >= 0 else "loss"
    fg           = data.get("fear_greed", {"value": 50, "label": "Neutral", "color": "#888"})

    # 심볼 가격 헤더
    price_tags = ""
    for s in data["symbols"]:
        chg_color = "#00c896" if s["change"] >= 0 else "#ff4d4d"
        chg_sign  = "+" if s["change"] >= 0 else ""
        price_tags += f"""
        <div class="price-tag">
            <span class="price-sym">{s['symbol'].replace('USDT','')}</span>
            <span class="price-val">${s['current']:,.0f}</span>
            <span style="color:{chg_color}; font-size:11px">{chg_sign}{s['change']:.2f}%</span>
        </div>
        """ if s["current"] else ""

    # 심볼 카드 + 차트
    cards = ""
    for s in data["symbols"]:
        pnl_color   = "profit" if s["realized"] >= 0 else "loss"
        unreal_color= "profit" if s["unrealized"] >= 0 else "loss"

        pos_html = ""
        if s["open"] and s["qty"] and s["entry_price"]:
            pos_html = f"""
            <div class="row"><span class="label">보유</span>
                <span>{s['qty']:.5f} @ ${s['entry_price']:,.2f}</span></div>
            <div class="row"><span class="label">투자금액</span>
                <span style="color:#aaa">${s['invested']:,.2f}</span></div>
            <div class="row"><span class="label">미실현</span>
                <span class="{unreal_color}">${s['unrealized']:+.2f}</span></div>
            """
        else:
            pos_html = '<div class="row"><span class="label">포지션</span><span style="color:#888">없음</span></div>'

        recent_rows = ""
        for t in s["recent"]:
            side_cls = "buy" if t["side"] == "BUY" else "sell"
            pnl_str  = f'<span class="{"profit" if t["pnl"] and t["pnl"]>0 else "loss"}">${t["pnl"]:+.2f}</span>' if t["pnl"] is not None else "-"
            recent_rows += f"""<tr>
                <td class="{side_cls}">{t['side']}</td>
                <td>${t['price']:,.0f}</td>
                <td>{t['qty']:.5f}</td>
                <td>{pnl_str}</td>
                <td style="color:#666">{t['time']}</td>
            </tr>"""

        table_html = f"""<table class="trade-table">
            <tr><th>방향</th><th>가격</th><th>수량</th><th>PnL</th><th>시간</th></tr>
            {recent_rows}
        </table>""" if recent_rows else ""

        cards += f"""
        <div class="card">
            <div class="card-header">
                <span class="symbol">{s['symbol']}</span>
                <span class="{pnl_color}" style="font-size:13px">손익 ${s['realized']:+.2f}</span>
            </div>
            <div id="chart-{s['symbol']}" style="height:180px; margin: 8px 0;"></div>
            <div class="row"><span class="label">실현손익</span>
                <span class="{pnl_color}">${s['realized']:+.2f}</span></div>
            {pos_html}
            <div class="row"><span class="label">거래</span>
                <span>BUY {s['buys']} / SELL {s['sells']} · 승률 {s['win_rate']}%</span></div>
            {table_html}
        </div>
        <script>
        (async () => {{
            const res = await fetch('/api/ohlcv/{s["symbol"]}?limit=100');
            const data = await res.json();
            const chart = LightweightCharts.createChart(document.getElementById('chart-{s["symbol"]}'), {{
                layout: {{ background: {{ color: '#1a1a1a' }}, textColor: '#888' }},
                grid: {{ vertLines: {{ color: '#2a2a2a' }}, horzLines: {{ color: '#2a2a2a' }} }},
                timeScale: {{ timeVisible: true, borderColor: '#2a2a2a' }},
                rightPriceScale: {{ borderColor: '#2a2a2a' }},
                width: document.getElementById('chart-{s["symbol"]}').clientWidth,
                height: 180,
            }});
            const series = chart.addCandlestickSeries({{
                upColor: '#00c896', downColor: '#ff4d4d',
                borderUpColor: '#00c896', borderDownColor: '#ff4d4d',
                wickUpColor: '#00c896', wickDownColor: '#ff4d4d',
            }});
            series.setData(data);
            chart.timeScale().fitContent();
            window.addEventListener('resize', () => {{
                chart.applyOptions({{ width: document.getElementById('chart-{s["symbol"]}').clientWidth }});
            }});
        }})();
        </script>
        """

    status_msg = "거래 없음 — 신호 대기 중" if not any(s["buys"] > 0 for s in data["symbols"]) else "Paper Trading 진행 중"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="60">
<title>Alpha Agents</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f0f0f; color: #e0e0e0; font-family: -apple-system, sans-serif; padding: 16px; max-width: 480px; margin: 0 auto; }}
  h1 {{ font-size: 18px; color: #fff; }}
  .subtitle {{ color: #555; font-size: 11px; margin-bottom: 12px; }}
  .prices {{ display: flex; gap: 12px; margin-bottom: 12px; }}
  .price-tag {{ background: #1a1a1a; border-radius: 10px; padding: 10px 14px; flex: 1; }}
  .price-sym {{ color: #888; font-size: 11px; display: block; }}
  .price-val {{ color: #fff; font-size: 16px; font-weight: bold; display: block; }}
  .summary {{ background: #1a1a1a; border-radius: 12px; padding: 16px; margin-bottom: 12px; }}
  .summary-top {{ display: flex; justify-content: space-between; align-items: flex-start; }}
  .big {{ font-size: 28px; font-weight: bold; }}
  .meta {{ font-size: 12px; color: #666; margin-top: 4px; }}
  .profit {{ color: #00c896; }}
  .loss {{ color: #ff4d4d; }}
  .card {{ background: #1a1a1a; border-radius: 12px; padding: 16px; margin-bottom: 12px; }}
  .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
  .symbol {{ font-size: 15px; font-weight: bold; color: #fff; }}
  .row {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #222; font-size: 13px; }}
  .label {{ color: #666; }}
  .trade-table {{ width: 100%; margin-top: 10px; font-size: 11px; border-collapse: collapse; }}
  .trade-table th {{ color: #555; text-align: left; padding: 3px; }}
  .trade-table td {{ padding: 3px; border-top: 1px solid #222; }}
  .buy {{ color: #00c896; font-weight: bold; }}
  .sell {{ color: #ff4d4d; font-weight: bold; }}
  .status {{ text-align: center; color: #444; font-size: 11px; margin-top: 16px; }}
  .fg-bar {{ background: #1a1a1a; border-radius: 12px; padding: 12px 16px; margin-bottom: 12px; display: flex; align-items: center; gap: 10px; }}
  .fg-label {{ color: #888; font-size: 12px; white-space: nowrap; }}
  .fg-track {{ flex: 1; background: #2a2a2a; border-radius: 4px; height: 6px; overflow: hidden; }}
  .fg-fill {{ height: 100%; border-radius: 4px; transition: width 0.3s; }}
</style>
</head>
<body>
<h1>Alpha Agents</h1>
<div class="subtitle">Paper Trading · 60초 자동갱신</div>

<div class="prices">{price_tags}</div>

<div class="fg-bar">
  <span class="fg-label">Fear & Greed</span>
  <div class="fg-track">
    <div class="fg-fill" style="width:{fg['value']}%; background:{fg['color']}"></div>
  </div>
  <span style="color:{fg['color']}; font-weight:bold">{fg['value']} {fg['label']}</span>
</div>

<div class="summary">
  <div class="summary-top">
    <div>
      <div style="color:#888; font-size:12px">총 잔고</div>
      <div class="big">${data['total_balance']:,.2f}</div>
      <div class="meta">초기 ${data['initial_capital']:,.0f} ·
        <span class="{return_color}">{'+' if return_pct>=0 else ''}{return_pct:.2f}%</span>
      </div>
    </div>
    <div style="text-align:right">
      <div style="color:#888; font-size:11px">총 손익</div>
      <div class="{total_color}" style="font-size:20px; font-weight:bold">${data['total_pnl']:+.2f}</div>
      <div style="color:#555; font-size:11px">실현 ${data['total_real']:+.2f}</div>
      <div style="color:#555; font-size:11px">가용 현금 ${data['portfolio_cash']:,.2f}</div>
      <div style="color:#555; font-size:11px">미실현 ${data['total_unreal']:+.2f}</div>
    </div>
  </div>
</div>

{cards}

<div class="status">{status_msg}</div>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
