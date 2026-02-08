# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Asgard API is a unified agent integration platform that provides an OpenAI-compatible gateway for managing and accessing AI agents. It's a FastAPI-based backend service with PostgreSQL persistence.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server with auto-reload
uvicorn app.main:app --reload

# Run with custom settings
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Run tests
pytest

# Run single test file
pytest tests/test_filename.py

# Run with coverage
pytest --cov=app

# Docker deployment (starts db + api)
docker-compose up -d

# Start database only
docker-compose up -d db
```

## Architecture

### Core Layers

1. **Entry Point** ([app/main.py](app/main.py)): FastAPI application setup, CORS middleware, lifespan events, and router inclusion

2. **Configuration** ([app/config.py](app/config.py)): Pydantic Settings using `pydantic-settings`. Uses `lru_cache()` for singleton pattern. All settings read from `.env` file.

3. **Database** ([app/database.py](app/database.py)): Async SQLAlchemy with `AsyncSession`. Key functions:
   - `init_db()`: Creates all tables via `Base.metadata.create_all`
   - `get_db()`: Dependency that yields session and handles commit/rollback

4. **Models** ([app/models.py](app/models.py)): SQLAlchemy declarative models:
   - `User`: Authentication + balance tracking
   - `APIKey`: Rate limiting + quota management (keys hashed with SHA256)
   - `Agent`: Agent metadata (agent_id format: `asgard/xxx`)
   - `UsageLog`: Token usage + cost tracking

5. **Authentication** ([app/auth.py](app/auth.py)):
   - JWT tokens for user auth (via `/api/v1/auth/login`)
   - API Key auth via `get_api_key_from_header` dependency
   - Keys hashed before storage; prefix shown to user on creation

6. **Schemas** ([app/schemas.py](app/schemas.py)): Pydantic models for API request/response validation

### API Routers

- `/api/v1/auth`: User registration, login, profile
- `/api/v1/agents`: Agent listing, details, enable/disable
- `/api/v1/chat`: Not used - chat is under `/v1`
- `/v1/chat/completions`: OpenAI-compatible chat completions (main endpoint)
- `/v1/models`: Lists available agents as OpenAI models

### Agent System ([app/agents/](app/agents/))

**Base Classes** ([base.py](app/agents/base.py)):
- `AgentEngine`: Abstract base with `run()` and `run_streaming()` methods
- `PromptTemplateAgent`: Extends AgentEngine with system prompt templating
- `StructuredAgent`: Extends PromptTemplateAgent for structured outputs

**Implementations** ([impl.py](app/agents/impl.py)):
- `CodeRefactorAgent`: System prompt for code analysis/refactoring
- `HanHanStyleAgent`: Chinese creative writing style
- `BusinessCopywritingAgent`, `UnitTestAgent`: Additional agents

**Agent Registry** ([chat.py:27-39](app/routers/chat.py#L27-L39)):
Agents are registered in-memory in `chat.py`. New agents must be added to `get_agent_engine()`.

### Request Flow

```
Request → Auth Dependency → Database Session → Agent Lookup → Agent Execution → Usage Log → Response
```

### Key Patterns

- **Dependencies**: FastAPI `Depends()` for auth, DB session injection
- **Async/Await**: All database operations are async (`AsyncSession`)
- **Streaming**: SSE (`sse-starlette`) for `/v1/chat/completions?stream=true`
- **Error Handling**: Global exception handler in `main.py` returns 500 with generic message

## Environment Variables

Key variables in `.env`:
- `DATABASE_URL`: PostgreSQL async connection string
- `JWT_SECRET_KEY`: Change in production
- `DEBUG`: Enables debug mode (CORS allows all origins)
- `RATE_LIMIT_PER_MINUTE`: Global rate limit
