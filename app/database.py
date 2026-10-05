"""
[WHO]: Provides async SQLAlchemy engine, session maker, get_db() dependency for request-scoped sessions, init_db/close_db() for lifecycle management
[FROM]: Depends on sqlalchemy.ext.asyncio for async engine and session, sqlalchemy.engine for database URL normalization, app.config.settings for database URL
[TO]: Consumed by main.py for lifespan events, routers for database operations, models for table creation
[HERE]: packages/api/app/database.py - Async database connection management; provides request-scoped sessions with automatic commit/rollback
"""
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from app.config import settings


_database_url = make_url(settings.database_url)

# Pool args (pool_size / max_overflow) are Postgres-only — SQLite uses NullPool
# and raises TypeError if pool config is passed. Detect the dialect so the same
# settings.database_url works for both `postgresql+asyncpg://...` (prod) and
# `sqlite+aiosqlite://...` (local demo / dev / single-machine deploy).
_engine_kwargs: dict = {
    "echo": settings.debug,
    "pool_pre_ping": True,
}
if not settings.database_url.startswith("sqlite"):
    _engine_kwargs["pool_size"] = 10
    _engine_kwargs["max_overflow"] = 20
    # Some managed Postgres providers expose URLs with `sslmode=require`.
    # SQLAlchemy forwards query params to asyncpg, whose connect() accepts
    # `ssl=True` instead of `sslmode=...`, so translate it at the engine boundary.
    sslmode = _database_url.query.get("sslmode")
    if sslmode:
        _database_url = _database_url.difference_update_query(["sslmode"])
        if sslmode not in {"disable", "allow", "prefer"}:
            _engine_kwargs["connect_args"] = {"ssl": True}

engine = create_async_engine(_database_url, **_engine_kwargs)

async_session = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncSession:
    """Dependency for getting database session"""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Initialize database tables"""
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "postgresql":
            accounting_version_exists = await conn.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'asgard_billing_usage_events'
                          AND column_name = 'accounting_version'
                    )
                    """
                )
            )
            if not accounting_version_exists:
                migration_path = (
                    Path(__file__).resolve().parents[1]
                    / "migrations"
                    / "20261005135722_hosted-credit-accounting.sql"
                )
                migration_sql = migration_path.read_text(encoding="utf-8")
                for statement in migration_sql.split(";"):
                    if statement.strip():
                        await conn.execute(text(statement))


async def close_db():
    """Close database connections"""
    await engine.dispose()
