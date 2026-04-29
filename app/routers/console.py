from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload
from dateutil.relativedelta import relativedelta

from app.database import get_db
from app.auth import get_user_from_jwt_or_apikey, generate_api_key, hash_api_key
from app.models import User, APIKey, UsageLog, Agent, BalanceTransaction
from app.schemas import (
    APIKeyResponse, APIKeyCreate, APIKeyCreateResponse,
    UsageStatsResponse, UsageLogResponse, BalanceResponse
)


router = APIRouter(prefix="/console", tags=["Developer Console"])


@router.get("/keys", response_model=List[APIKeyResponse])
async def list_api_keys(
    current_user: User = Depends(get_user_from_jwt_or_apikey),
    db: AsyncSession = Depends(get_db)
):
    """List all API keys for current user"""
    result = await db.execute(
        select(APIKey).where(APIKey.user_id == current_user.id)
    )
    keys = result.scalars().all()
    return keys


@router.post("/keys", response_model=APIKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    key_data: APIKeyCreate = None,
    current_user: User = Depends(get_user_from_jwt_or_apikey),
    db: AsyncSession = Depends(get_db)
):
    """Create new API key"""
    key, prefix = generate_api_key()
    key_hash = hash_api_key(key)

    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=prefix,
        name=key_data.name if key_data else None,
        user_id=current_user.id,
        rate_limit=key_data.rate_limit if key_data else 60,
        quota_limit=key_data.quota_limit,
        ip_whitelist=key_data.ip_whitelist if key_data else [],
        expires_at=key_data.expires_at,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    return APIKeyCreateResponse(
        name=api_key.name,
        api_key=key,
        key_prefix=prefix
    )


@router.delete("/keys/{key_uuid}")
async def delete_api_key(
    key_uuid: str,
    current_user: User = Depends(get_user_from_jwt_or_apikey),
    db: AsyncSession = Depends(get_db)
):
    """Delete API key"""
    result = await db.execute(
        select(APIKey).where(
            APIKey.uuid == key_uuid,
            APIKey.user_id == current_user.id
        )
    )
    key = result.scalar_one_or_none()

    if not key:
        raise HTTPException(status_code=404, detail="API key not found")

    await db.delete(key)
    await db.commit()

    return {"status": "deleted"}


@router.post("/keys/{key_uuid}/rotate", response_model=APIKeyCreateResponse)
async def rotate_api_key(
    key_uuid: str,
    current_user: User = Depends(get_user_from_jwt_or_apikey),
    db: AsyncSession = Depends(get_db)
):
    """Rotate (regenerate) API key"""
    # Delete old key
    result = await db.execute(
        select(APIKey).where(
            APIKey.uuid == key_uuid,
            APIKey.user_id == current_user.id
        )
    )
    old_key = result.scalar_one_or_none()

    if not old_key:
        raise HTTPException(status_code=404, detail="API key not found")

    # Create new key
    new_key, prefix = generate_api_key()
    new_key_hash = hash_api_key(new_key)

    old_key.key_hash = new_key_hash
    old_key.key_prefix = prefix
    old_key.created_at = datetime.utcnow()
    old_key.used_quota = 0  # Reset usage

    await db.commit()

    return APIKeyCreateResponse(
        name=old_key.name,
        api_key=new_key,
        key_prefix=prefix
    )


@router.get("/usage/stats", response_model=UsageStatsResponse)
async def get_usage_stats(
    period: str = Query("week", regex="^(day|week|month)$"),
    current_user: User = Depends(get_user_from_jwt_or_apikey),
    db: AsyncSession = Depends(get_db)
):
    """Get usage statistics"""
    # Calculate time range
    now = datetime.utcnow()
    if period == "day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_date = (now - relativedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start_date = (now - relativedelta(months=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    # Get user's API keys
    result = await db.execute(
        select(APIKey.id).where(APIKey.user_id == current_user.id)
    )
    key_ids = [row[0] for row in result.fetchall()]

    if not key_ids:
        return {
            "total_requests": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_cost": 0,
            "by_agent": {}
        }

    # Query usage logs
    result = await db.execute(
        select(UsageLog).where(
            and_(
                UsageLog.api_key_id.in_(key_ids),
                UsageLog.created_at >= start_date
            )
        ).order_by(UsageLog.created_at.desc())
    )
    logs = result.scalars().all()

    # Aggregate stats
    total_requests = len(logs)
    total_prompt_tokens = sum(log.prompt_tokens for log in logs)
    total_completion_tokens = sum(log.completion_tokens for log in logs)
    total_cost = sum(log.cost for log in logs)

    # Group by agent
    by_agent = {}
    for log in logs:
        agent_name = log.model
        if agent_name not in by_agent:
            by_agent[agent_name] = {
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost": 0
            }
        by_agent[agent_name]["requests"] += 1
        by_agent[agent_name]["prompt_tokens"] += log.prompt_tokens
        by_agent[agent_name]["completion_tokens"] += log.completion_tokens
        by_agent[agent_name]["cost"] += log.cost

    return {
        "total_requests": total_requests,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "total_cost": total_cost,
        "by_agent": by_agent
    }


@router.get("/usage/logs", response_model=List[UsageLogResponse])
async def get_usage_logs(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_user_from_jwt_or_apikey),
    db: AsyncSession = Depends(get_db)
):
    """Get usage logs"""
    result = await db.execute(
        select(APIKey.id).where(APIKey.user_id == current_user.id)
    )
    key_ids = [row[0] for row in result.fetchall()]

    result = await db.execute(
        select(UsageLog)
        .where(UsageLog.api_key_id.in_(key_ids))
        .order_by(UsageLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    logs = result.scalars().all()

    return logs


@router.get("/balance", response_model=BalanceResponse)
async def get_balance(
    current_user: User = Depends(get_user_from_jwt_or_apikey)
):
    """Get current balance"""
    return {
        "balance": current_user.balance,
        "currency": "Credit"
    }
