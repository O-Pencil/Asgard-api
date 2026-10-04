"""
[WHO]: Provides SQLAlchemy declarative models: User, APIKey, Agent, UsageLog, BalanceTransaction, BillingCustomer, BillingPlan, BillingPrice, BillingSubscription, BillingUsagePeriod, BillingUsageWindow, BillingUsageEvent, BillingCreditBalance, BillingCreditGrant, BillingWebhookEvent with relationships and constraints
[FROM]: Depends on SQLAlchemy for ORM, uuid for UUID generation, datetime for timestamps
[TO]: Consumed by database.py for table creation, routers for CRUD operations, services for business logic
[HERE]: packages/api/app/models.py - Database schema definitions; core data model for multi-tenant agent management
"""
from datetime import datetime
from typing import Optional, List
from sqlalchemy import Column, Integer, String, Text, DateTime, Float, Boolean, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import uuid

from app.config import settings


Base = declarative_base()


def table_name(name: str) -> str:
    """Prefix Asgard tables so shared cloud databases avoid collisions."""
    return f"{settings.db_table_prefix}{name}"


def generate_uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    """用户表"""
    __tablename__ = table_name("users")

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255))
    balance = Column(Float, default=0.0)  # 余额（Credit）
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    api_keys = relationship("APIKey", back_populates="user")
    usage_logs = relationship("UsageLog", back_populates="user")


class APIKey(Base):
    """API Key 表"""
    __tablename__ = table_name("api_keys")

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True)
    key_hash = Column(String(255), unique=True, index=True, nullable=False)  # 单向哈希存储
    key_prefix = Column(String(20), index=True)  # 前缀（用于识别）
    name = Column(String(255))
    user_id = Column(Integer, ForeignKey(f"{User.__tablename__}.id"), nullable=False)
    rate_limit = Column(Integer, default=60)  # 每分钟请求限制
    quota_limit = Column(Float, default=None)  # 额度上限（Credit）
    used_quota = Column(Float, default=0.0)  # 已使用额度
    ip_whitelist = Column(JSON, default=list)  # IP 白名单
    is_active = Column(Boolean, default=True)
    last_used_at = Column(DateTime)
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="api_keys")
    usage_logs = relationship("UsageLog", back_populates="api_key")


class Agent(Base):
    """Agent 表"""
    __tablename__ = table_name("agents")

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True)
    agent_id = Column(String(100), unique=True, index=True, nullable=False)  # asgard/xxx 格式
    name = Column(String(255), nullable=False)
    description = Column(Text)
    category = Column(String(50), index=True)  # dev, writing, creative, analysis
    capabilities = Column(JSON, default=list)  # 能力标签列表
    context_window = Column(String(20))  # 64K, 128K, 256K
    pricing = Column(Float)  # Credit/1K Tokens
    parameters = Column(JSON, default=dict)  # Agent 参数配置
    is_active = Column(Boolean, default=True)
    is_public = Column(Boolean, default=True)  # 是否公开
    version = Column(String(20), default="1.0.0")

    # ─── P1 (doc 16 §7.5): Agent 三种形态分类 ─────────────────────────────
    # super     : 平台 / 厂商分发的 immutable SuperAgent（出现在"市场"列表）
    # derived   : 用户从某个 super 派生出的个性化版本（parent_template_id 指向 super）
    # custom    : 用户从零自创（无 parent）
    kind = Column(String(16), default="custom", nullable=False, index=True)

    # 当 kind=derived 时，指向其父 SuperAgent 的 id（self-ref FK，但用 Integer
    # 而非 ForeignKey 以避免循环引用复杂性 — 应用层校验）。
    parent_template_id = Column(Integer, nullable=True, index=True)

    # immutable    : Soul 不可被本地用户修改（super 默认；强制由 Gateway 端执行）
    # overridable  : 可被本地用户调整（derived / custom 默认）
    soul_policy = Column(String(16), default="overridable", nullable=False)
    # ──────────────────────────────────────────────────────────────────

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    usage_logs = relationship("UsageLog", back_populates="agent")


class UsageLog(Base):
    """调用记录表"""
    __tablename__ = table_name("usage_logs")

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True)
    user_id = Column(Integer, ForeignKey(f"{User.__tablename__}.id"), nullable=False)
    api_key_id = Column(Integer, ForeignKey(f"{APIKey.__tablename__}.id"), nullable=False)
    agent_id = Column(Integer, ForeignKey(f"{Agent.__tablename__}.id"), nullable=False)

    # 请求信息
    model = Column(String(100))  # asgard/xxx
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    cost = Column(Float, default=0.0)  # 消耗的 Credit

    # 响应信息
    status = Column(String(20), default="success")  # success, error
    error_message = Column(Text)
    latency_ms = Column(Integer)  # 响应延迟

    # 客户端信息
    client_ip = Column(String(45))
    user_agent = Column(String(500))

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    user = relationship("User", back_populates="usage_logs")
    api_key = relationship("APIKey", back_populates="usage_logs")
    agent = relationship("Agent", back_populates="usage_logs")


class BalanceTransaction(Base):
    """余额记录表"""
    __tablename__ = table_name("balance_transactions")

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True)
    user_id = Column(Integer, ForeignKey(f"{User.__tablename__}.id"), nullable=False)
    amount = Column(Float, nullable=False)  # 正数为充值，负数为扣费
    transaction_type = Column(String(50))  # deposit, usage, refund
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class BillingCustomer(Base):
    """Billing customer keyed by email/license identity for Catea Pro."""
    __tablename__ = table_name("billing_customers")

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    provider = Column(String(32), default="creem", nullable=False, index=True)
    provider_customer_id = Column(String(128), index=True)
    license_key = Column(String(64), unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    subscriptions = relationship("BillingSubscription", back_populates="customer")
    usage_periods = relationship("BillingUsagePeriod", back_populates="customer")
    usage_windows = relationship("BillingUsageWindow", back_populates="customer")
    usage_events = relationship("BillingUsageEvent", back_populates="customer")
    credit_balance = relationship("BillingCreditBalance", back_populates="customer", uselist=False)
    credit_grants = relationship("BillingCreditGrant", back_populates="customer")


class BillingPlan(Base):
    """Subscription plan definition for Catea-hosted entitlements."""
    __tablename__ = table_name("billing_plans")

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(String(64), unique=True, index=True, nullable=False)
    name = Column(String(128), nullable=False)
    billing_period = Column(String(32), default="monthly", nullable=False)
    monthly_credits = Column(Integer, default=0, nullable=False)
    window_credits = Column(Integer, default=0, nullable=False)
    window_hours = Column(Integer, default=5, nullable=False)
    features = Column(JSON, default=dict)
    active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    prices = relationship("BillingPrice", back_populates="plan")
    usage_periods = relationship("BillingUsagePeriod", back_populates="plan")
    usage_windows = relationship("BillingUsageWindow", back_populates="plan")


class BillingPrice(Base):
    """Provider-specific price/product mapping for one plan and currency."""
    __tablename__ = table_name("billing_prices")
    __table_args__ = (
        UniqueConstraint("provider", "provider_product_id", name="uq_billing_provider_product"),
    )

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey(f"{BillingPlan.__tablename__}.id"), nullable=False)
    currency = Column(String(8), nullable=False, index=True)
    amount = Column(Integer, default=0, nullable=False)
    provider = Column(String(32), default="creem", nullable=False, index=True)
    provider_product_id = Column(String(128), nullable=False, index=True)
    provider_price_id = Column(String(128), index=True)
    active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    plan = relationship("BillingPlan", back_populates="prices")


class BillingSubscription(Base):
    """Provider subscription state used to decide whether Catea Pro is active."""
    __tablename__ = table_name("billing_subscriptions")
    __table_args__ = (
        UniqueConstraint("provider", "provider_subscription_id", name="uq_billing_provider_subscription"),
    )

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True)
    customer_id = Column(Integer, ForeignKey(f"{BillingCustomer.__tablename__}.id"), nullable=False)
    provider = Column(String(32), default="creem", nullable=False, index=True)
    provider_subscription_id = Column(String(128), nullable=False, index=True)
    provider_order_id = Column(String(128), index=True)
    provider_checkout_id = Column(String(128), index=True)
    product_id = Column(String(128), index=True)
    plan = Column(String(64), nullable=False, index=True)
    status = Column(String(64), nullable=False, index=True)
    active = Column(Boolean, default=False, nullable=False, index=True)
    current_period_start = Column(DateTime)
    current_period_end = Column(DateTime)
    canceled_at = Column(DateTime)
    provider_metadata = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("BillingCustomer", back_populates="subscriptions")


class BillingUsagePeriod(Base):
    """Monthly included-usage bucket for a billing customer."""
    __tablename__ = table_name("billing_usage_periods")
    __table_args__ = (
        UniqueConstraint("customer_id", "period_start", "period_end", name="uq_billing_usage_period"),
    )

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True)
    customer_id = Column(Integer, ForeignKey(f"{BillingCustomer.__tablename__}.id"), nullable=False)
    subscription_id = Column(Integer, ForeignKey(f"{BillingSubscription.__tablename__}.id"))
    plan_id = Column(Integer, ForeignKey(f"{BillingPlan.__tablename__}.id"), nullable=False)
    period_start = Column(DateTime, nullable=False, index=True)
    period_end = Column(DateTime, nullable=False, index=True)
    included_credits = Column(Integer, default=0, nullable=False)
    used_credits = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("BillingCustomer", back_populates="usage_periods")
    plan = relationship("BillingPlan", back_populates="usage_periods")


class BillingUsageWindow(Base):
    """Short reset window for Codex-like hosted usage availability."""
    __tablename__ = table_name("billing_usage_windows")
    __table_args__ = (
        UniqueConstraint("customer_id", "window_start", "window_end", name="uq_billing_usage_window"),
    )

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True)
    customer_id = Column(Integer, ForeignKey(f"{BillingCustomer.__tablename__}.id"), nullable=False)
    plan_id = Column(Integer, ForeignKey(f"{BillingPlan.__tablename__}.id"), nullable=False)
    window_start = Column(DateTime, nullable=False, index=True)
    window_end = Column(DateTime, nullable=False, index=True)
    included_credits = Column(Integer, default=0, nullable=False)
    used_credits = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("BillingCustomer", back_populates="usage_windows")
    plan = relationship("BillingPlan", back_populates="usage_windows")


class BillingUsageEvent(Base):
    """Append-only hosted model usage event for audit and future billing."""
    __tablename__ = table_name("billing_usage_events")
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_billing_usage_request"),
    )

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True)
    customer_id = Column(Integer, ForeignKey(f"{BillingCustomer.__tablename__}.id"), nullable=False)
    request_id = Column(String(128), nullable=False, index=True)
    model_route = Column(String(128), nullable=False)
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    credits = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    customer = relationship("BillingCustomer", back_populates="usage_events")


class BillingCreditBalance(Base):
    """Purchased hosted-model credit balance for a billing customer."""
    __tablename__ = table_name("billing_credit_balances")
    __table_args__ = (
        UniqueConstraint("customer_id", name="uq_billing_credit_balance_customer"),
    )

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True)
    customer_id = Column(Integer, ForeignKey(f"{BillingCustomer.__tablename__}.id"), nullable=False)
    balance_credits = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("BillingCustomer", back_populates="credit_balance")


class BillingCreditGrant(Base):
    """Append-only record of purchased or manually granted hosted-model credits."""
    __tablename__ = table_name("billing_credit_grants")
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_billing_credit_grant_provider_event"),
    )

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True)
    customer_id = Column(Integer, ForeignKey(f"{BillingCustomer.__tablename__}.id"), nullable=False)
    provider = Column(String(32), default="waffo", nullable=False, index=True)
    provider_event_id = Column(String(128), nullable=False, index=True)
    product_id = Column(String(128), index=True)
    credits = Column(Integer, default=0, nullable=False)
    currency = Column(String(8))
    amount = Column(Float)
    metadata = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    customer = relationship("BillingCustomer", back_populates="credit_grants")


class BillingWebhookEvent(Base):
    """Processed billing webhook deliveries for idempotency and debugging."""
    __tablename__ = table_name("billing_webhook_events")
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_billing_provider_event"),
    )

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True)
    provider = Column(String(32), default="creem", nullable=False, index=True)
    provider_event_id = Column(String(128), nullable=False, index=True)
    event_type = Column(String(128), nullable=False, index=True)
    processed = Column(Boolean, default=False, nullable=False)
    payload = Column(JSON, default=dict)
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime)
