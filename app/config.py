"""
[WHO]: Provides Settings Pydantic model with lru_cache() singleton pattern for configuration management, loads from environment variables and .env file
[FROM]: Depends on pydantic_settings for BaseSettings, functools.lru_cache for singleton
[TO]: Consumed by all modules (main.py, auth.py, database.py, cache.py, routers) for accessing configuration values
[HERE]: packages/api/app/config.py - Configuration management; singleton pattern ensures one Settings instance throughout application lifetime
"""
from functools import lru_cache
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/asgard"
    db_table_prefix: str = "asgard_"

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
    pencil_gateway_hostport: Optional[str] = None
    pencil_gateway_internal_key: str = ""
    pencil_gateway_connect_timeout_s: float = 5.0
    pencil_gateway_read_timeout_s: Optional[float] = None
    pencil_default_provider: Optional[str] = None
    pencil_default_model: Optional[str] = None

    # Billing for Catea Pro
    billing_provider: str = "creem"
    creem_api_key: str = ""
    creem_api_base: str = "https://test-api.creem.io"
    creem_environment: str = "test"
    creem_product_id_pro_monthly: str = ""
    creem_product_id_pro_yearly: str = ""
    creem_webhook_id: str = ""
    creem_webhook_secret: str = ""
    catea_billing_success_url: str = "https://asgard-api-utj6.onrender.com/billing/success"
    catea_pro_monthly_credits: int = 100000
    catea_pro_window_credits: int = 20000
    catea_pro_window_hours: int = 5
    creem_product_id_pro_monthly_cny: str = ""
    waffo_api_base: str = "https://waffo-pancake-auth-service.vercel.app"
    waffo_environment: str = "test"
    waffo_merchant_id: str = ""
    waffo_store_id: str = ""
    waffo_private_key: str = ""
    waffo_product_id_pro_monthly: str = ""
    waffo_webhook_public_key: str = ""
    waffo_content_safety_enabled: bool = False
    waffo_content_safety_api_base: str = "https://api.waffo.ai"
    waffo_content_safety_locale: str = "zh"
    waffo_content_safety_semantic: str = "enforce"
    xorpay_api_base: str = "https://xorpay.com"
    xorpay_aid: str = ""
    xorpay_app_secret: str = ""
    xorpay_notify_url: str = ""
    xorpay_pay_type: str = "cashier"
    xorpay_pro_monthly_price_cny: str = "18.00"
    catea_hosted_model_api_key: str = ""
    catea_hosted_model_base_url: str = "https://api.minimax.io/v1"
    catea_hosted_model_name: str = "MiniMax-M3.1-Flash-Preview"
    catea_hosted_model_reasoning_effort: str = "medium"
    catea_hosted_model_timeout_s: float = 120.0

    # Single-user hosted preview mode. This only controls deployment/runtime
    # behavior; production auth hardening should stay separate.
    single_user_mode: bool = False
    admin_email: str = "admin@asgard.dev"
    admin_password: str = "password"

    # Logging
    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        """Accept provider Postgres URLs and normalize them for async SQLAlchemy."""
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value[len("postgres://"):]
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value[len("postgresql://"):]
        return value

    @field_validator("pencil_gateway_read_timeout_s", mode="before")
    @classmethod
    def empty_timeout_is_none(cls, value):
        if value == "":
            return None
        return value

    @field_validator("db_table_prefix")
    @classmethod
    def normalize_table_prefix(cls, value: str) -> str:
        if not value:
            return ""
        safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in value)
        if safe and not safe.endswith("_"):
            safe += "_"
        return safe

    @model_validator(mode="after")
    def apply_render_private_service_urls(self):
        if self.pencil_gateway_hostport:
            self.pencil_gateway_url = f"http://{self.pencil_gateway_hostport}"
        return self

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
