# packages/api/app/middleware/

> P2 | Parent: ../../AGENTS.md

## Module Overview

Middleware for request processing. Currently implements rate limiting using Redis to enforce per-minute request quotas for each API Key.

---

## Member List

rate_limit.py: Rate limiting middleware using Redis, checks API key's rate_limit field, tracks request count per minute with Redis INCR/EXPIRE, returns HTTP 429 with Retry-After header when exceeded, integrated in main.py via @app.middleware("http")

---

Rule: Members complete, one item per line, parent links valid, precise terms first