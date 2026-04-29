# packages/api/app/routers/

> P2 | Parent: ../../AGENTS.md

## Module Overview

API route definitions. Organized by domain: authentication, agent management, chat completions, and developer console. Each router includes endpoint definitions, request/response schemas, authentication dependencies, and database session injection.

---

## Member List

__init__.py: Router package initialization, aggregates and re-exports auth, agents, chat, console routers

auth.py: Authentication endpoints: POST /api/v1/auth/register (user creation with password hashing), POST /api/v1/auth/login (JWT token generation), GET /api/v1/auth/profile (current user info), uses Depends(get_db) and bcrypt for password verification

agents.py: Agent management endpoints: GET /api/v1/agents (list all active agents), GET /api/v1/agents/{id} (agent details), POST /api/v1/agents/enable (activate agent), POST /api/v1/agents/disable (deactivate agent), admin controls with Depends(get_api_key_from_header)

chat.py: OpenAI-compatible chat endpoint: POST /v1/chat/completions (supports stream=true for SSE streaming), get_agent_engine() registry maps agent_id (asgard/xxx, pencil/xxx) to AgentEngine implementations, usage logging for token tracking and cost calculation

console.py: Developer console endpoints: GET /api/v1/console/keys (list user's API keys), POST /api/v1/console/keys (create new key with SHA256 hashing), DELETE /api/v1/console/keys/{id} (revoke key), GET /api/v1/console/usage (usage statistics by agent), requires JWT authentication

---

Rule: Members complete, one item per line, parent links valid, precise terms first