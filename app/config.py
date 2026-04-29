"""
[WHO]: Provides Settings Pydantic model with lru_cache() singleton pattern for configuration management, loads from environment variables and .env file
[FROM]: Depends on pydantic_settings for BaseSettings, functools.lru_cache for singleton
[TO]: Consumed by all modules (main.py, auth.py, database.py, cache.py, routers) for accessing configuration values
[HERE]: packages/api/app/config.py - Configuration management; singleton pattern ensures one Settings instance throughout application lifetime
"""
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

    # Redis Cache
    redis_url: str = ""  # Redis connection URL, empty means disabled

    # Ollama (Local LLM)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5"
    use_ollama: bool = True  # Set to False to use OpenAI API instead

    # Pencil Agent Gateway
    pencil_gateway_url: str = "http://pencil-gateway:8080"
    pencil_gateway_internal_key: str = ""
    pencil_gateway_connect_timeout_s: float = 5.0
    pencil_gateway_read_timeout_s: Optional[float] = None

    # Logging
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
