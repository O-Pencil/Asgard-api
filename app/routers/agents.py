from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.auth import get_current_user
from app.models import Agent, User, APIKey
from app.schemas import AgentResponse, AgentListResponse


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
