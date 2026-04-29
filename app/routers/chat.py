"""
[WHO]: Provides /v1/chat/completions endpoint with OpenAI-compatible API, streaming support, agent routing, usage logging
[FROM]: Depends on app.services.pencil_gateway for pencil/* agents, app.agents.base for built-in agents, app.auth for authentication, app.models for DB models, app.schemas for request/response schemas
[TO]: Called by external clients (Cursor, VS Code, nanopencil-editor) for chat completions
[HERE]: packages/api/app/routers/chat.py - OpenAI-compatible chat endpoint; routes requests to built-in agents or Pencil Gateway based on agent_id
"""

import json
import time
import asyncio
import logging
from datetime import datetime
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.database import get_db
from app.auth import get_api_key_from_header, get_current_user
from app.models import APIKey, User, Agent, UsageLog
from app.schemas import ChatCompletionRequest, ChatCompletionResponse, Message
from app.config import settings
from app.agents.base import AgentEngine
from app.agents.impl import CodeRefactorAgent, HanHanStyleAgent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat Completion"])


# ---------------------------------------------------------------------------
# Agent Engine Registry (built-in asgard/* agents)
# ---------------------------------------------------------------------------

_agent_registry: dict[str, AgentEngine] = {}


def get_agent_engine(agent_id: str) -> AgentEngine:
    """Get or create built-in agent engine instance"""
    if agent_id not in _agent_registry:
        if agent_id == "asgard/code-refactor":
            _agent_registry[agent_id] = CodeRefactorAgent()
        elif agent_id == "asgard/hanhan-style":
            _agent_registry[agent_id] = HanHanStyleAgent()
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{agent_id}' not found"
            )
    return _agent_registry[agent_id]


# ---------------------------------------------------------------------------
# Pencil Agent helpers
# ---------------------------------------------------------------------------

def is_pencil_agent(agent: Agent) -> bool:
    """
    Check if an agent is a Pencil Agent (routed to Pencil Agent Gateway).

    Judgment is based on DB agent record, not just user-supplied model field.
    """
    params = agent.parameters or {}
    return (
        agent.agent_id.startswith("pencil/")
        or params.get("agent_type") == "pencil-agent"
    )


def enforce_user_can_call(api_key: APIKey, agent: Agent):
    """
    Verify the API key owner is allowed to call this agent.

    - Public asgard/* agents: allowed for all authenticated users.
    - pencil/* agents: must match owner_user_id.
    """
    params = agent.parameters or {}
    if params.get("agent_type") == "pencil-agent" or agent.agent_id.startswith("pencil/"):
        owner_user_id = params.get("owner_user_id")
        if owner_user_id is not None and str(owner_user_id) != str(api_key.user_id):
            raise HTTPException(
                status_code=403,
                detail="Agent not allowed for this API key"
            )


# ---------------------------------------------------------------------------
# PencilGateway singleton accessor
# ---------------------------------------------------------------------------

_pencil_gateway = None


def get_pencil_gateway():
    """
    Get the global PencilAgentBackend instance.
    Set during app startup in main.py lifespan.
    """
    if _pencil_gateway is None:
        raise HTTPException(
            status_code=503,
            detail="Pencil Agent Gateway not configured"
        )
    return _pencil_gateway


def set_pencil_gateway(gateway):
    """Set the global PencilAgentBackend instance (called from main.py)."""
    global _pencil_gateway
    _pencil_gateway = gateway


# ---------------------------------------------------------------------------
# Token counting & usage log helpers
# ---------------------------------------------------------------------------

async def count_tokens(text: str) -> int:
    """Simple token counter (rough estimate)"""
    return len(text) // 4


async def save_usage_log(
    db: AsyncSession,
    user: User,
    api_key: APIKey,
    agent: Agent,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    latency_ms: int,
    log_status: str,
    error_message: str = None
):
    """Save usage log"""
    log = UsageLog(
        user_id=user.id,
        api_key_id=api_key.id,
        agent_id=agent.id,
        model=agent.agent_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        cost=cost,
        status=log_status,
        error_message=error_message,
        latency_ms=latency_ms,
        client_ip="",
    )
    db.add(log)
    await db.commit()


# ---------------------------------------------------------------------------
# Chat Completions endpoint
# ---------------------------------------------------------------------------

@router.post("/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    raw_request: Request,
    api_key: APIKey = Depends(get_api_key_from_header),
    db: AsyncSession = Depends(get_db)
):
    """
    OpenAI-compatible Chat Completions endpoint.

    Routes to:
    - pencil/* agents  -> Pencil Agent Gateway (proxy)
    - asgard/* agents  -> Built-in AgentEngine (local)
    """
    start_time = time.time()

    # --- Load agent from DB ---
    agent_id = request.model
    result = await db.execute(
        select(Agent).where(Agent.agent_id == agent_id)
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found"
        )

    if not agent.is_active:
        raise HTTPException(
            status_code=403,
            detail=f"Agent '{agent_id}' is not available"
        )

    # --- Load user ---
    result = await db.execute(
        select(User).where(User.id == api_key.user_id)
    )
    user = result.scalar_one_or_none()

    # --- Permission check ---
    enforce_user_can_call(api_key, agent)

    # --- Quota check ---
    prompt_text = "\n".join([m.content for m in request.messages])
    prompt_tokens = await count_tokens(prompt_text)
    estimated_cost = (prompt_tokens / 1000) * (agent.pricing or 0.02)

    if api_key.quota_limit:
        if api_key.used_quota + estimated_cost > api_key.quota_limit:
            raise HTTPException(
                status_code=429,
                detail="API key quota exceeded"
            )

    # ================================================================
    # Pencil Agent branch — proxy to Gateway
    # ================================================================
    if is_pencil_agent(agent):
        gateway = get_pencil_gateway()
        body = request.model_dump(exclude_none=True)

        # For non-streaming: record usage after response
        # For streaming: record estimated usage (stream usage tracking is P1)
        if not request.stream:
            response = await gateway.proxy_chat(
                request=raw_request,
                body=body,
                user=user,
                agent=agent,
            )
            # Record usage for non-streaming pencil agent calls
            latency_ms = int((time.time() - start_time) * 1000)
            try:
                await save_usage_log(
                    db=db, user=user, api_key=api_key, agent=agent,
                    prompt_tokens=prompt_tokens, completion_tokens=0,
                    cost=estimated_cost, latency_ms=latency_ms, log_status="success"
                )
                # Update quota
                api_key.used_quota += estimated_cost
                db.add(api_key)
                await db.commit()
            except Exception as e:
                logger.warning(f"Failed to record pencil agent usage: {e}")
            return response
        else:
            # Streaming: proxy directly, usage tracked as estimated
            return await gateway.proxy_chat(
                request=raw_request,
                body=body,
                user=user,
                agent=agent,
            )

    # ================================================================
    # Built-in Agent branch — local AgentEngine
    # ================================================================
    engine = get_agent_engine(agent_id)

    if request.stream:
        # --- Streaming response ---
        async def generate_stream():
            stream_completed = False
            try:
                async for chunk in engine.run_streaming(
                    messages=request.messages,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens
                ):
                    yield {
                        "data": json.dumps(chunk, ensure_ascii=False)
                    }
                    await asyncio.sleep(0)
                yield {"data": "[DONE]"}
                stream_completed = True
            except Exception as e:
                yield {
                    "data": json.dumps({
                        "error": {
                            "message": str(e),
                            "type": "server_error"
                        }
                    })
                }
            finally:
                if stream_completed:
                    latency_ms = int((time.time() - start_time) * 1000)
                    api_key.used_quota += estimated_cost
                    db.add(api_key)
                    await db.commit()
                    await db.refresh(api_key)

        return EventSourceResponse(
            generate_stream(),
            media_type="text/event-stream"
        )

    else:
        # --- Non-streaming response ---
        try:
            response = await engine.run(
                messages=request.messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens
            )

            completion_tokens = await count_tokens(response)
            total_tokens = prompt_tokens + completion_tokens
            actual_cost = (total_tokens / 1000) * (agent.pricing or 0.02)

            api_key.used_quota += actual_cost
            latency_ms = int((time.time() - start_time) * 1000)

            await save_usage_log(
                db=db, user=user, api_key=api_key, agent=agent,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                cost=actual_cost, latency_ms=latency_ms, log_status="success"
            )

            return ChatCompletionResponse(
                id=f"chatcmpl-{int(time.time())}",
                created=int(time.time()),
                model=agent_id,
                choices=[{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response
                    },
                    "finish_reason": "stop"
                }],
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens
                }
            )

        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            await save_usage_log(
                db=db, user=user, api_key=api_key, agent=agent,
                prompt_tokens=prompt_tokens, completion_tokens=0,
                cost=0, latency_ms=latency_ms, log_status="error",
                error_message=str(e)
            )
            raise HTTPException(status_code=500, detail=str(e))
