import json
import time
import asyncio
from datetime import datetime
from typing import AsyncGenerator
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


router = APIRouter(prefix="/chat", tags=["Chat Completion"])


# Agent Engine Registry
_agent_registry: dict[str, AgentEngine] = {}


def get_agent_engine(agent_id: str) -> AgentEngine:
    """Get or create agent engine instance"""
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


async def count_tokens(text: str) -> int:
    """Simple token counter (rough estimate)"""
    # In production, use tiktoken
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
    status: str,
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
        status=status,
        error_message=error_message,
        latency_ms=latency_ms,
        client_ip="",  # Get from request
    )
    db.add(log)
    await db.commit()


@router.post("/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    api_key: APIKey = Depends(get_api_key_from_header),
    db: AsyncSession = Depends(get_db)
):
    """
    OpenAI-compatible Chat Completions endpoint

    Compatible with:
    - OpenAI Chat Completions API format
    - Cursor, Continue, and other IDE integrations
    """
    start_time = time.time()

    # Extract agent_id from model field
    agent_id = request.model

    # Get agent info
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

    # Get user
    result = await db.execute(
        select(User).where(User.id == api_key.user_id)
    )
    user = result.scalar_one_or_none()

    # Calculate estimated cost
    prompt_text = "\n".join([m.content for m in request.messages])
    prompt_tokens = await count_tokens(prompt_text)
    estimated_cost = (prompt_tokens / 1000) * (agent.pricing or 0.02)

    # Check quota limits
    if api_key.quota_limit:
        if api_key.used_quota + estimated_cost > api_key.quota_limit:
            raise HTTPException(
                status_code=429,
                detail="API key quota exceeded"
            )

    # Get agent engine
    engine = get_agent_engine(agent_id)

    if request.stream:
        # Streaming response
        async def generate_stream():
            try:
                async for chunk in engine.run_streaming(
                    messages=request.messages,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens
                ):
                    yield {
                        "data": json.dumps(chunk, ensure_ascii=False)
                    }
                    await asyncio.sleep(0)  # Yield control
                yield {"data": "[DONE]"}
            except Exception as e:
                yield {
                    "data": json.dumps({
                        "error": {
                            "message": str(e),
                            "type": "server_error"
                        }
                    })
                }

        response = EventSourceResponse(
            generate_stream(),
            media_type="text/event-stream"
        )

        # Calculate usage and persist quota update
        latency_ms = int((time.time() - start_time) * 1000)
        api_key.used_quota += estimated_cost
        db.add(api_key)
        await db.commit()
        await db.refresh(api_key)

        return response

    else:
        # Non-streaming response
        try:
            response = await engine.run(
                messages=request.messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens
            )

            completion_tokens = await count_tokens(response)
            total_tokens = prompt_tokens + completion_tokens
            actual_cost = (total_tokens / 1000) * (agent.pricing or 0.02)

            # Update quota
            api_key.used_quota += actual_cost

            latency_ms = int((time.time() - start_time) * 1000)

            # Save usage log
            await save_usage_log(
                db=db,
                user=user,
                api_key=api_key,
                agent=agent,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=actual_cost,
                latency_ms=latency_ms,
                status="success"
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
                db=db,
                user=user,
                api_key=api_key,
                agent=agent,
                prompt_tokens=prompt_tokens,
                completion_tokens=0,
                cost=0,
                latency_ms=latency_ms,
                status="error",
                error_message=str(e)
            )
            raise HTTPException(status_code=500, detail=str(e))
