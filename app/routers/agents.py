from typing import List, Optional
import logging
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.auth import get_current_user
from app.models import Agent, User, APIKey
from app.schemas import AgentResponse, AgentListResponse, PencilAgentCreateRequest, PencilAgentCreateResponse
from app.routers.chat import get_pencil_gateway


router = APIRouter(prefix="/agents", tags=["Agents"])


@router.get("", response_model=AgentListResponse)
async def list_agents(
    category: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all available agents"""
    query = select(Agent).where(Agent.is_active == True, Agent.is_public == True)

    # Filter by category
    if category and category != "all":
        query = query.where(Agent.category == category)

    # Search by name or description
    if search:
        search_term = f"%{search}%"
        query = query.where(
            (Agent.name.ilike(search_term)) |
            (Agent.description.ilike(search_term))
        )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    agents = result.scalars().all()

    return {
        "agents": [AgentResponse.model_validate(agent) for agent in agents],
        "total": total
    }


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get agent details"""
    result = await db.execute(
        select(Agent).where(Agent.agent_id == agent_id)
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if not agent.is_active:
        raise HTTPException(status_code=404, detail="Agent not available")

    return agent


@router.post("/{agent_id}/enable")
async def enable_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Enable an agent for current user"""
    result = await db.execute(
        select(Agent).where(Agent.agent_id == agent_id)
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # For now, just return success - implementation would track user-agent relationship
    return {"status": "enabled", "agent_id": agent_id}


@router.post("/{agent_id}/disable")
async def disable_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Disable an agent for current user"""
    # Implementation similar to enable
    return {"status": "disabled", "agent_id": agent_id}


@router.post("/pencil", response_model=PencilAgentCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_pencil_agent(
    body: PencilAgentCreateRequest,
    raw_request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new Pencil Agent instance.

    - Generates a gateway_agent_id from user uuid + name.
    - Inserts Agent record with agent_id = pencil/<gateway_agent_id>.
    - Calls Gateway POST /v1/agents to create the runtime instance.
    - On Gateway failure, keeps the DB record with status=error for retry.
    """
    # Generate unique gateway agent ID
    gateway_agent_id = f"asgard-u{current_user.id}-{uuid.uuid4().hex[:8]}"
    agent_id = f"pencil/{gateway_agent_id}"

    # Build parameters JSON
    parameters = {
        "agent_type": "pencil-agent",
        "gateway_agent_id": gateway_agent_id,
        "owner_user_id": current_user.id,
        "soul": {
            "systemPrompt": body.soul_prompt or f"你是{body.name}。",
            "styleTags": body.style_tags or [],
        },
        "memory": {
            "mode": "short-term",
            "maxTurns": body.memory_max_turns,
        },
    }

    # Add optional model config
    if body.model_provider or body.model_name:
        parameters["model"] = {}
        if body.model_provider:
            parameters["model"]["provider"] = body.model_provider
        if body.model_name:
            parameters["model"]["name"] = body.model_name

    parameters["gateway_status"] = "syncing"

    # Create Agent record in DB
    agent = Agent(
        agent_id=agent_id,
        name=body.name,
        description=body.description,
        category=body.category,
        capabilities=["pencil-agent"],
        pricing=0.02,
        parameters=parameters,
        is_active=True,
        is_public=body.is_public,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    # Sync with Gateway
    try:
        gateway = get_pencil_gateway()
        await gateway.create_agent(
            request=raw_request,
            user=current_user,
            agent=agent,
        )
        parameters["gateway_status"] = "ready"
        parameters["last_synced_at"] = datetime.utcnow().isoformat()
    except Exception as e:
        logging.getLogger(__name__).error(f"Gateway sync failed: {e}")
        parameters["gateway_status"] = "error"
        parameters["gateway_error"] = str(e)

    # Update parameters in DB
    agent.parameters = parameters
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    return PencilAgentCreateResponse(
        agent_id=agent_id,
        gateway_agent_id=gateway_agent_id,
        name=body.name,
        status=parameters["gateway_status"],
        created_at=agent.created_at,
    )
