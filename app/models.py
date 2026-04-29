"""
[WHO]: Provides SQLAlchemy declarative models: User, APIKey, Agent, UsageLog, BalanceTransaction with relationships and constraints
[FROM]: Depends on SQLAlchemy for ORM, uuid for UUID generation, datetime for timestamps
[TO]: Consumed by database.py for table creation, routers for CRUD operations, services for business logic
[HERE]: packages/api/app/models.py - Database schema definitions; core data model for multi-tenant agent management
"""
from datetime import datetime
from typing import Optional, List
from sqlalchemy import Column, Integer, String, Text, DateTime, Float, Boolean, ForeignKey, JSON
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
    key_prefix = Column(String(10), index=True)  # 前缀（用于识别）
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
