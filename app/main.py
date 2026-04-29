"""
[WHO]: Provides FastAPI application setup, CORS middleware, lifespan events, router inclusion, /v1/models endpoint for OpenAI compatibility
[FROM]: Depends on FastAPI for web framework, SQLAlchemy for DB, Redis for cache, PencilAgentBackend for gateway integration
[TO]: Consumed by uvicorn ASGI server for HTTP serving, routers for endpoint registration, middleware for request processing
[HERE]: packages/api/app/main.py - FastAPI application entry point; orchestrates all components and serves as root router
"""

"""
Asgard API - Main Application Entry Point

Unified Agent Integration Platform
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.config import settings
from app.database import init_db, close_db, get_db
from app.cache import init_cache, close_cache
from app.middleware.rate_limit import rate_limit_middleware
from app.routers import auth, agents, chat, console
from app.services.pencil_gateway import PencilAgentBackend
from app.auth import (
    get_user_from_jwt_or_apikey,
    get_password_hash,
    generate_api_key,
    hash_api_key,
)
from app.models import Agent, APIKey, User


# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Single-User Mode bootstrap
# ---------------------------------------------------------------------------

async def _ensure_single_user_admin():
    """
    In SINGLE_USER_MODE, ensure the admin user exists (with a default API key
    for usage tracking).  Safe to call on every startup — idempotent.
    """
    if not settings.single_user_mode:
        return

    from app.database import async_session

    async with async_session() as session:
        # --- Ensure admin user ---
        result = await session.execute(
            select(User).where(User.email == settings.admin_email)
        )
        admin = result.scalar_one_or_none()

        if not admin:
            admin = User(
                email=settings.admin_email,
                hashed_password=get_password_hash(settings.admin_password),
                full_name="Admin",
                balance=1000.0,  # Generous starting balance for experience
                is_active=True,
            )
            session.add(admin)
            await session.commit()
            await session.refresh(admin)
            logger.info("Single-user mode: created admin user", extra={"email": settings.admin_email})
        else:
            # Update password in case config changed
            admin.hashed_password = get_password_hash(settings.admin_password)
            await session.commit()
            logger.info("Single-user mode: admin user exists", extra={"email": settings.admin_email})

        # --- Ensure admin has at least one API key ---
        result = await session.execute(
            select(APIKey).where(APIKey.user_id == admin.id, APIKey.is_active == True)
        )
        existing_key = result.scalar_one_or_none()

        if not existing_key:
            raw_key, prefix = generate_api_key()
            default_key = APIKey(
                key_hash=hash_api_key(raw_key),
                key_prefix=prefix,
                name="single-user-default",
                user_id=admin.id,
                rate_limit=120,
            )
            session.add(default_key)
            await session.commit()
            logger.info("Single-user mode: created default API key for admin")
        else:
            logger.info("Single-user mode: admin already has API key(s)")

    logger.info("Single-user mode: admin bootstrap complete")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    logger.info("Starting Asgard API...")
    await init_db()
    logger.info("Database initialized")

    # Single-user mode bootstrap (must be after init_db)
    await _ensure_single_user_admin()

    await init_cache()
    logger.info("Cache initialized")

    # Initialize Pencil Agent Gateway backend
    if settings.pencil_gateway_internal_key:
        gateway = PencilAgentBackend(settings)
        chat.set_pencil_gateway(gateway)
        logger.info("Pencil Agent Gateway backend initialized")
    else:
        logger.warning("PENCIL_GATEWAY_INTERNAL_KEY not set — pencil/* agents unavailable")

    yield

    # Shutdown
    logger.info("Shutting down Asgard API...")
    await close_cache()
    logger.info("Cache connections closed")
    await close_db()
    logger.info("Database connections closed")


# Create FastAPI application
app = FastAPI(
    title="Asgard API",
    description="Unified Agent Integration Platform - OpenAI Compatible Gateway",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# CORS middleware
cors_origins = settings.allowed_hosts.split(",") if settings.allowed_hosts else []
if settings.debug or settings.single_user_mode:
    # 调试模式 / 体验版：允许所有来源
    cors_origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting middleware
@app.middleware("http")
async def rate_limit(request: Request, call_next):
    return await rate_limit_middleware(request, call_next)


# Exception handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


# Health check
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "version": "0.1.0"}


# OpenAI compatibility — models from DB
@app.get("/v1/models")
async def list_models(
    current_user: User = Depends(get_user_from_jwt_or_apikey),
    db=Depends(get_db)
):
    """
    List models visible to the calling user:
      - All public, active agents (Marketplace).
      - Plus the caller's own private PencilAgents (so editor / OpenAI clients
        can address `pencil/<gateway_agent_id>` for the keys they actually own).

    Visibility filter runs in Python for cross-DB portability (Agent.parameters
    is plain JSON; cheap at v0.1 user volume).
    """
    result = await db.execute(select(Agent).where(Agent.is_active == True))
    candidates = result.scalars().all()
    user_id = str(current_user.id)

    visible = [
        a for a in candidates
        if a.is_public
        or (
            (a.parameters or {}).get("agent_type") == "pencil-agent"
            and str((a.parameters or {}).get("owner_user_id")) == user_id
        )
    ]

    return {
        "object": "list",
        "data": [
            {
                "id": agent.agent_id,
                "object": "model",
                "created": int(agent.created_at.timestamp()) if agent.created_at else 0,
                "owned_by": "asgard",
            }
            for agent in visible
        ],
    }


# Include routers
app.include_router(auth.router, prefix="/api/v1")
app.include_router(agents.router, prefix="/api/v1")
app.include_router(console.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/v1")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug
    )
