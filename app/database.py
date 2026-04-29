"""
[WHO]: Provides async SQLAlchemy engine, session maker, get_db() dependency for request-scoped sessions, init_db/close_db() for lifecycle management
[FROM]: Depends on sqlalchemy.ext.asyncio for async engine and session, app.config.settings for database URL
[TO]: Consumed by main.py for lifespan events, routers for database operations, models for table creation
[HERE]: packages/api/app/database.py - Async database connection management; provides request-scoped sessions with automatic commit/rollback
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from app.config import settings


engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

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


async def close_db():
    """Close database connections"""
    await engine.dispose()
