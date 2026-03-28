"""
Railway 통합 실행: 트레이딩 봇 + 웹 대시보드 동시 실행
"""
import asyncio
import os
import uvicorn
from main import main as trading_main
from web.app import app


async def run_all():
    port = int(os.environ.get("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)

    await asyncio.gather(
        trading_main(),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(run_all())
