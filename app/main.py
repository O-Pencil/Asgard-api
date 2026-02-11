"""
Asgard API - Main Application Entry Point

Unified Agent Integration Platform
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import init_db, close_db
from app.cache import init_cache, close_cache
from app.middleware.rate_limit import rate_limit_middleware
from app.routers import auth, agents, chat, console


# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    logger.info("Starting Asgard API...")
    await init_db()
    logger.info("Database initialized")
    await init_cache()
    logger.info("Cache initialized")

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
# 安全配置：生产环境使用明确的白名单，调试模式使用允许的主机列表
cors_origins = settings.allowed_hosts.split(",") if settings.allowed_hosts else []
if settings.debug:
    # 调试模式：使用明确的白名单，不允许所有来源
    cors_origins = cors_origins
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


# OpenAI compatibility info
@app.get("/v1/models")
async def list_models():
    """List available models (agents)"""
    return {
        "object": "list",
        "data": [
            {
                "id": "asgard/code-refactor",
                "object": "model",
                "created": 0,
                "owned_by": "asgard"
            },
            {
                "id": "asgard/hanhan-style",
                "object": "model",
                "created": 0,
                "owned_by": "asgard"
            }
        ]
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
