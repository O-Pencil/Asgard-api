from functools import lru_cache
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/asgard"

    # JWT
    jwt_secret_key: str = "your-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    debug: bool = False
    allowed_hosts: str = ""  # 逗号分隔的域名列表，生产环境使用

    # OpenAI Compatibility
    openai_api_base: str = "http://localhost:8000/v1"

    # Rate Limiting
    rate_limit_per_minute: int = 60

    # Logging
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
