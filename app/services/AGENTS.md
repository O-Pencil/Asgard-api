# packages/api/app/services/

> P2 | Parent: ../../AGENTS.md

## Module Overview

External service integrations. Provides bridge to nanoPencil Agent Gateway for pencil/* prefixed agents, enabling access to external agent ecosystem.

---

## Member List

__init__.py: Services package initialization, exports PencilAgentBackend and related service functions

pencil_gateway.py: Pencil Agent Gateway integration, provides PencilAgentBackend class with execute_agent() method, makes HTTP calls to nanoPencil API with PENCIL_GATEWAY_INTERNAL_KEY, routes pencil/* agent requests to external gateway, handles streaming responses for pencil agents

---

Rule: Members complete, one item per line, parent links valid, precise terms first