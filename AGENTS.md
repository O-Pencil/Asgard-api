# packages/api/

> P2 | Parent: ../AGENTS.md

## Module Overview

FastAPI backend for Asgard Platform. Provides OpenAI-compatible API gateway, agent registry, multi-tenant user management, and agent execution engine. Uses async SQLAlchemy with PostgreSQL for persistence, Redis for caching, and Ollama for LLM inference.

---

## Member List

app/__init__.py: Package initialization, empty file for Python module structure

app/main.py: FastAPI application entry point, sets up CORS, middleware, routers, lifespan events, includes /v1/models endpoint for OpenAI compatibility

app/config.py: Pydantic Settings with lru_cache() singleton pattern, reads DATABASE_URL, JWT_SECRET_KEY, DEBUG, RATE_LIMIT_PER_MINUTE, PENCIL_GATEWAY_INTERNAL_KEY from environment

app/database.py: Async SQLAlchemy session management, provides init_db() for table creation and get_db() dependency for request-scoped sessions

app/cache.py: Redis connection management, provides init_cache() and close_cache() for lifespan events

app/auth.py: JWT token generation/verification for user authentication, API Key header extraction and validation, Depends() injection for protected routes

app/models.py: SQLAlchemy declarative models: User, APIKey, Agent, UsageLog, BalanceTransaction; relationships cascade correctly for foreign keys

app/schemas.py: Pydantic models for API request/response validation: UserCreate, UserLogin, AgentCreate, AgentResponse, ChatRequest, ChatResponse

app/agents/__init__.py: Agent engine package initialization, exports AgentEngine base class and implementations

app/agents/base.py: Abstract base classes: AgentEngine (run/run_streaming), PromptTemplateAgent (system prompt template), StructuredAgent (output format specification)

app/agents/impl.py: Concrete agent implementations: CodeRefactorAgent, HanHanStyleAgent, BusinessCopywritingAgent, UnitTestAgent with specific system prompts

app/routers/__init__.py: Router package initialization, aggregates and re-exports all routers

app/routers/auth.py: Authentication endpoints: POST /register, POST /login, GET /profile; JWT token generation, password hashing with bcrypt

app/routers/agents.py: Agent management endpoints: GET /agents (list), GET /agents/{id} (details), POST /agents/enable, POST /agents/disable; admin controls

app/routers/chat.py: OpenAI-compatible chat endpoint: POST /v1/chat/completions (streaming support), get_agent_engine() registry maps agent_id to implementations

app/routers/console.py: Developer console endpoints: GET /console/keys (list API keys), POST /console/keys (create), DELETE /console/keys/{id}, GET /console/usage (usage statistics)

app/middleware/rate_limit.py: Rate limiting middleware using Redis, enforces API_KEY.rate_limit per minute, returns 429 on exceed

app/services/__init__.py: Services package initialization, exports external service integrations

app/services/pencil_gateway.py: Pencil Agent Gateway integration, provides PencilAgentBackend for pencil/* agent routing, external API calls to nanoPencil

app/llm/__init__.py: LLM provider package initialization, exports provider factory functions

app/llm/ollama.py: Ollama provider implementation, provides get_ollama_provider(), chat_once() for non-streaming, chat() for streaming responses

---

Rule: Members complete, one item per line, parent links valid, precise terms first