from .postgres_manager import get_pool, close_pool, init_tables
from .redis_manager import get_client, close_client


async def init_db():
    await init_tables()


__all__ = ["get_pool", "close_pool", "init_tables", "init_db", "get_client", "close_client"]
