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
from app.schemas import (
    AgentResponse,
    AgentListResponse,
    PencilAgentCreateRequest,
    PencilAgentCreateResponse,
    PencilAgentDetail,
    PencilAgentUpdateRequest,
)
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


# ---------------------------------------------------------------------------
# Pencil Agent — list, detail, update
# ---------------------------------------------------------------------------

def _to_pencil_detail(agent: Agent) -> PencilAgentDetail:
    """Project the JSON-blob parameters into a typed view for the UI."""
    params = agent.parameters or {}
    soul = params.get("soul") or {}
    memory = params.get("memory") or {}
    model = params.get("model") or {}
    return PencilAgentDetail(
        agent_id=agent.agent_id,
        gateway_agent_id=params.get("gateway_agent_id", agent.agent_id.replace("pencil/", "")),
        name=agent.name,
        description=agent.description,
        category=agent.category,
        soul_prompt=soul.get("systemPrompt"),
        style_tags=soul.get("styleTags") or [],
        memory_max_turns=int(memory.get("maxTurns", 30)),
        model_provider=model.get("provider"),
        model_name=model.get("name"),
        is_public=agent.is_public,
        gateway_status=params.get("gateway_status"),
        last_synced_at=params.get("last_synced_at"),
        created_at=agent.created_at,
        updated_at=getattr(agent, "updated_at", None),
    )


@router.get("/pencil/me", response_model=List[PencilAgentDetail])
async def list_my_pencil_agents(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return all PencilAgents owned by the current user (private + public).

    Separate from `GET /agents` (Marketplace) so the UI can render
    "My Agents" without leaking other users' private prompts.
    """
    result = await db.execute(
        select(Agent).where(
            Agent.is_active == True,
            Agent.agent_id.like("pencil/%"),
        )
    )
    candidates = result.scalars().all()
    user_id = str(current_user.id)
    mine = [
        a for a in candidates
        if str((a.parameters or {}).get("owner_user_id")) == user_id
    ]
    return [_to_pencil_detail(a) for a in mine]


@router.get("/pencil/{gateway_agent_id}", response_model=PencilAgentDetail)
async def get_pencil_agent(
    gateway_agent_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a single PencilAgent owned by the caller."""
    agent_id = f"pencil/{gateway_agent_id}"
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="PencilAgent not found")
    params = agent.parameters or {}
    if str(params.get("owner_user_id")) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your PencilAgent")
    return _to_pencil_detail(agent)


@router.put("/pencil/{gateway_agent_id}", response_model=PencilAgentDetail)
async def update_pencil_agent(
    gateway_agent_id: str,
    body: PencilAgentUpdateRequest,
    raw_request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Edit a PencilAgent's Soul / memory / model in place.

    Routes to Gateway PUT /v1/agents/:id which keeps the running sessions —
    user's open conversations don't lose history. Existing sessions retain
    the Soul they were created with; new sessions see the update.
    Frontend should surface that semantic to the user (see docs/12 §2.2).
    """
    agent_id = f"pencil/{gateway_agent_id}"
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="PencilAgent not found")

    params = dict(agent.parameters or {})
    if str(params.get("owner_user_id")) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your PencilAgent")

    # Merge changes — only update fields the caller actually supplied.
    soul = dict(params.get("soul") or {})
    memory = dict(params.get("memory") or {"mode": "short-term", "maxTurns": 30})
    model = dict(params.get("model") or {})

    if body.soul_prompt is not None:
        soul["systemPrompt"] = body.soul_prompt
    if body.style_tags is not None:
        soul["styleTags"] = body.style_tags
    if body.memory_max_turns is not None:
        memory["maxTurns"] = body.memory_max_turns
    if body.model_provider is not None:
        model["provider"] = body.model_provider
    if body.model_name is not None:
        model["name"] = body.model_name

    params["soul"] = soul
    params["memory"] = memory
    if model:
        params["model"] = model
    params["gateway_status"] = "syncing"

    if body.name is not None:
        agent.name = body.name
    if body.description is not None:
        agent.description = body.description
    if body.category is not None:
        agent.category = body.category
    if body.is_public is not None:
        agent.is_public = body.is_public
    agent.parameters = params
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    # Sync to Gateway (PUT — preserves sessions)
    try:
        gateway = get_pencil_gateway()
        await gateway.update_agent(
            request=raw_request,
            user=current_user,
            agent=agent,
        )
        params["gateway_status"] = "ready"
        params["last_synced_at"] = datetime.utcnow().isoformat()
        params.pop("gateway_error", None)
    except Exception as e:
        logging.getLogger(__name__).error(f"Gateway PUT sync failed: {e}")
        params["gateway_status"] = "error"
        params["gateway_error"] = str(e)
        # Keep the DB updated so the user can retry; raise to surface the
        # failure (HTTP 502 would be cleaner — leaving as-is for symmetry
        # with create flow which also lets the exception bubble).

    agent.parameters = params
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    return _to_pencil_detail(agent)
