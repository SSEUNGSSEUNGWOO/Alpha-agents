from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Binance
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True

    # Glassnode
    glassnode_api_key: str = ""

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "alpha_agents"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379

    # 트레이딩 설정
    trading_symbols: str = "BTCUSDT,ETHUSDT"
    trading_interval: str = "15m"
    prediction_horizon: int = 32       # 32봉 × 15m = 8시간
    max_position_ratio: float = 0.25
    mdd_circuit_breaker: float = 0.15

    # Phase 2+: newszips
    newszips_supabase_url: str = ""
    newszips_supabase_key: str = ""

    @property
    def symbols(self) -> list[str]:
        return [s.strip() for s in self.trading_symbols.split(",")]

    # Railway가 자동으로 주입하는 DB URL (있으면 우선 사용)
    database_url: str = ""

    @property
    def postgres_dsn(self) -> str:
        if self.database_url:
            return self.database_url.replace("postgresql+asyncpg://", "postgresql://")
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def async_postgres_dsn(self) -> str:
        if self.database_url:
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
