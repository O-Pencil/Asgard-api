"""
[WHO]: Provides admin-only read endpoints for users, subscriptions, billing quota, and provider quota placeholders
[FROM]: Depends on FastAPI request handling, SQLAlchemy async sessions, app.auth JWT helpers, app.config settings, app.models billing/user tables
[TO]: Consumed by main.py as /admin routes for the Asgard admin dashboard
[HERE]: packages/api/app/routers/admin.py - Admin dashboard API; reads billing and usage state from the database and restricts access to ADMIN_EMAIL
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import decode_jwt_to_user_id
from app.config import settings
from app.database import get_db
from app.models import (
    BillingCustomer,
    BillingPlan,
    BillingSubscription,
    BillingUsageEvent,
    BillingUsagePeriod,
    BillingUsageWindow,
    User,
)


router = APIRouter(prefix="/admin", tags=["Admin"])


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def require_admin_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Require an explicit admin JWT; do not fall back to single-user mode."""
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required")
    user_id = decode_jwt_to_user_id(token)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin user")
    if user.email.lower() != settings.admin_email.lower():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def _dt(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _quota(bucket: BillingUsagePeriod | BillingUsageWindow | None) -> Optional[dict]:
    if not bucket:
        return None
    included = bucket.included_credits or 0
    used = bucket.used_credits or 0
    used_percent = round(min(100, (used / included) * 100), 2) if included > 0 else 0
    return {
        "included_credits": included,
        "used_credits": used,
        "used_percent": used_percent,
        "remaining_percent": max(0, round(100 - used_percent, 2)),
        "reset_at": _dt(getattr(bucket, "period_end", None) or getattr(bucket, "window_end", None)),
    }


async def _billing_snapshot(db: AsyncSession):
    users = (await db.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
    customers = (await db.execute(select(BillingCustomer))).scalars().all()
    subscriptions = (
        (await db.execute(select(BillingSubscription).order_by(BillingSubscription.updated_at.desc())))
        .scalars()
        .all()
    )
    periods = (
        (await db.execute(select(BillingUsagePeriod).order_by(BillingUsagePeriod.period_end.desc())))
        .scalars()
        .all()
    )
    windows = (
        (await db.execute(select(BillingUsageWindow).order_by(BillingUsageWindow.window_end.desc())))
        .scalars()
        .all()
    )
    events = (
        (await db.execute(select(BillingUsageEvent).order_by(BillingUsageEvent.created_at.desc()).limit(20)))
        .scalars()
        .all()
    )
    plans = (await db.execute(select(BillingPlan).order_by(BillingPlan.plan_id.asc()))).scalars().all()

    customer_by_email = {customer.email.lower(): customer for customer in customers}
    subscription_by_customer = {}
    for subscription in subscriptions:
        subscription_by_customer.setdefault(subscription.customer_id, subscription)
    period_by_customer = {}
    for period in periods:
        period_by_customer.setdefault(period.customer_id, period)
    window_by_customer = {}
    for window in windows:
        window_by_customer.setdefault(window.customer_id, window)

    return {
        "users": users,
        "customers": customers,
        "subscriptions": subscriptions,
        "events": events,
        "plans": plans,
        "customer_by_email": customer_by_email,
        "subscription_by_customer": subscription_by_customer,
        "period_by_customer": period_by_customer,
        "window_by_customer": window_by_customer,
    }


@router.get("/overview")
async def admin_overview(
    _: User = Depends(require_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Return aggregate admin metrics for subscriptions and hosted-model usage."""
    snapshot = await _billing_snapshot(db)
    users = snapshot["users"]
    customers = snapshot["customers"]
    subscriptions = snapshot["subscriptions"]
    events = snapshot["events"]
    active_subscriptions = [subscription for subscription in subscriptions if subscription.active]
    total_credits = sum(event.credits for event in events)

    return {
        "totals": {
            "users": len(users),
            "billing_customers": len(customers),
            "subscriptions": len(subscriptions),
            "active_subscriptions": len(active_subscriptions),
            "recent_hosted_credits": total_credits,
            "recent_hosted_events": len(events),
        },
        "plans": [
            {
                "plan_id": plan.plan_id,
                "name": plan.name,
                "billing_period": plan.billing_period,
                "monthly_credits": plan.monthly_credits,
                "window_credits": plan.window_credits,
                "window_hours": plan.window_hours,
                "active": plan.active,
            }
            for plan in snapshot["plans"]
        ],
        "provider_quota": {
            "provider": "minimax",
            "configured": bool(settings.catea_hosted_model_api_key),
            "base_url": settings.catea_hosted_model_base_url,
            "model": settings.catea_hosted_model_name,
            "balance": None,
            "note": "MiniMax provider-level quota endpoint is not wired yet.",
        },
        "recent_events": [
            {
                "request_id": event.request_id,
                "customer_id": event.customer_id,
                "model_route": event.model_route,
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
                "credits": event.credits,
                "created_at": _dt(event.created_at),
            }
            for event in events
        ],
    }


@router.get("/users")
async def admin_users(
    _: User = Depends(require_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Return users joined with billing customer, subscription, and quota state."""
    snapshot = await _billing_snapshot(db)
    rows = []
    for user in snapshot["users"]:
        customer = snapshot["customer_by_email"].get(user.email.lower())
        subscription = (
            snapshot["subscription_by_customer"].get(customer.id)
            if customer
            else None
        )
        period = snapshot["period_by_customer"].get(customer.id) if customer else None
        window = snapshot["window_by_customer"].get(customer.id) if customer else None
        rows.append(
            {
                "user": {
                    "uuid": user.uuid,
                    "email": user.email,
                    "full_name": user.full_name,
                    "balance": user.balance,
                    "is_active": user.is_active,
                    "created_at": _dt(user.created_at),
                },
                "billing_customer": {
                    "uuid": customer.uuid,
                    "provider": customer.provider,
                    "provider_customer_id": customer.provider_customer_id,
                    "license_key": customer.license_key,
                    "created_at": _dt(customer.created_at),
                }
                if customer
                else None,
                "subscription": {
                    "uuid": subscription.uuid,
                    "provider": subscription.provider,
                    "plan": subscription.plan,
                    "status": subscription.status,
                    "active": subscription.active,
                    "current_period_start": _dt(subscription.current_period_start),
                    "current_period_end": _dt(subscription.current_period_end),
                    "product_id": subscription.product_id,
                    "updated_at": _dt(subscription.updated_at),
                }
                if subscription
                else None,
                "quota": {
                    "monthly": _quota(period),
                    "window": _quota(window),
                },
            }
        )
    return {"users": rows}
