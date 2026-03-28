"""
Railway 통합 실행: 트레이딩 봇 + 웹 대시보드 동시 실행
"""
import asyncio
import uvicorn
from main import main as trading_main
from web.app import app


async def run_all():
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)

    await asyncio.gather(
        trading_main(),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(run_all())
