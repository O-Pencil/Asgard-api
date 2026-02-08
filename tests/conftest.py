"""
Test configuration and fixtures for Asgard API tests.
"""
import pytest
import asyncio
from typing import AsyncGenerator, Generator
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

# Set environment before importing app modules
import os
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-testing"
os.environ["DEBUG"] = "true"
os.environ["ALLOWED_HOSTS"] = ""

from app.models import Base
from app.main import app
from app.database import get_db
from app.auth import get_password_hash, create_access_token, hash_api_key, generate_api_key
from app.models import User, APIKey, Agent


# Create in-memory SQLite engine for testing
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=False,
)

test_session_factory = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def test_db_setup():
    """Create database tables for testing."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db_session(test_db_setup) -> AsyncGenerator[AsyncSession, None]:
    """Create a fresh database session for each test."""
    async with test_session_factory() as session:
        yield session
        await session.rollback()
        await session.close()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create test client with database session override."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def test_user(db_session: AsyncSession) -> User:
    """Create a test user."""
    user = User(
        email="testuser@example.com",
        hashed_password=get_password_hash("testpassword123"),
        full_name="Test User",
        balance=100.0,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def test_inactive_user(db_session: AsyncSession) -> User:
    """Create an inactive test user."""
    user = User(
        email="inactive@example.com",
        hashed_password=get_password_hash("testpassword123"),
        full_name="Inactive User",
        balance=0.0,
        is_active=False,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def test_api_key(db_session: AsyncSession, test_user: User) -> tuple[APIKey, str]:
    """Create a test API key."""
    api_key_value, prefix = generate_api_key()
    api_key = APIKey(
        key_hash=hash_api_key(api_key_value),
        key_prefix=prefix,
        name="Test API Key",
        user_id=test_user.id,
        rate_limit=60,
        quota_limit=10.0,
        used_quota=0.0,
        is_active=True,
    )
    db_session.add(api_key)
    await db_session.commit()
    await db_session.refresh(api_key)
    return api_key, api_key_value


@pytest.fixture
async def test_expired_api_key(db_session: AsyncSession, test_user: User) -> tuple[APIKey, str]:
    """Create an expired test API key."""
    api_key_value, prefix = generate_api_key()
    api_key = APIKey(
        key_hash=hash_api_key(api_key_value),
        key_prefix=prefix,
        name="Expired API Key",
        user_id=test_user.id,
        rate_limit=60,
        quota_limit=10.0,
        used_quota=0.0,
        is_active=True,
        expires_at=datetime.utcnow() - timedelta(days=1),  # Expired
    )
    db_session.add(api_key)
    await db_session.commit()
    await db_session.refresh(api_key)
    return api_key, api_key_value


@pytest.fixture
async def test_disabled_api_key(db_session: AsyncSession, test_user: User) -> tuple[APIKey, str]:
    """Create a disabled test API key."""
    api_key_value, prefix = generate_api_key()
    api_key = APIKey(
        key_hash=hash_api_key(api_key_value),
        key_prefix=prefix,
        name="Disabled API Key",
        user_id=test_user.id,
        rate_limit=60,
        quota_limit=10.0,
        used_quota=0.0,
        is_active=False,
    )
    db_session.add(api_key)
    await db_session.commit()
    await db_session.refresh(api_key)
    return api_key, api_key_value


@pytest.fixture
async def test_api_key_low_quota(db_session: AsyncSession, test_user: User) -> tuple[APIKey, str]:
    """Create an API key with low quota (near limit)."""
    api_key_value, prefix = generate_api_key()
    api_key = APIKey(
        key_hash=hash_api_key(api_key_value),
        key_prefix=prefix,
        name="Low Quota API Key",
        user_id=test_user.id,
        rate_limit=60,
        quota_limit=5.0,
        used_quota=4.9,  # Near quota limit
        is_active=True,
    )
    db_session.add(api_key)
    await db_session.commit()
    await db_session.refresh(api_key)
    return api_key, api_key_value


@pytest.fixture
async def test_agents(db_session: AsyncSession) -> list[Agent]:
    """Create test agents."""
    agents = [
        Agent(
            agent_id="asgard/code-refactor",
            name="Code Refactor Agent",
            description="Code analysis and refactoring assistant",
            category="development",
            capabilities=["code-analysis", "refactoring", "best-practices"],
            context_window="128K",
            pricing=0.02,
            is_active=True,
            is_public=True,
            version="1.0.0",
        ),
        Agent(
            agent_id="asgard/hanhan-style",
            name="Han Han Style Agent",
            description="Chinese creative writing in Han Han style",
            category="writing",
            capabilities=["creative-writing", "satire", "philosophy"],
            context_window="64K",
            pricing=0.015,
            is_active=True,
            is_public=True,
            version="1.0.0",
        ),
        Agent(
            agent_id="asgard/business-copy",
            name="Business Copywriting Agent",
            description="Professional business copywriting assistant",
            category="marketing",
            capabilities=["copywriting", "marketing", "branding"],
            context_window="64K",
            pricing=0.018,
            is_active=True,
            is_public=True,
            version="1.0.0",
        ),
        Agent(
            agent_id="asgard/unit-test",
            name="Unit Test Agent",
            description="Unit test generation assistant",
            category="development",
            capabilities=["unit-testing", "test-coverage", "qa"],
            context_window="64K",
            pricing=0.02,
            is_active=False,  # Inactive agent for testing
            is_public=True,
            version="1.0.0",
        ),
    ]
    for agent in agents:
        db_session.add(agent)
    await db_session.commit()
    for agent in agents:
        await db_session.refresh(agent)
    return agents


@pytest.fixture
def auth_headers(test_user: User) -> dict:
    """Generate JWT auth headers."""
    token = create_access_token(data={"sub": str(test_user.id), "email": test_user.email})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api_key_headers(api_key_value: str) -> dict:
    """Generate API key auth headers."""
    return {"X-API-Key": api_key_value}


@pytest.fixture
def chat_completion_payload() -> dict:
    """Generate a valid chat completion request payload."""
    return {
        "model": "asgard/code-refactor",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello, how are you?"}
        ],
        "temperature": 0.7,
        "max_tokens": 100,
        "stream": False
    }
