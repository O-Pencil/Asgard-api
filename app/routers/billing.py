"""
[WHO]: Provides billing router for Creem checkout creation, webhook fulfillment, license status lookup, and payment success response
[FROM]: Depends on FastAPI request handling, SQLAlchemy async sessions, httpx for Creem API calls, hmac/hashlib for webhook signature verification, app.config settings, app.models billing tables, app.schemas billing DTOs
[TO]: Consumed by main.py as /billing routes for Catea Pro payment and entitlement testing
[HERE]: packages/api/app/routers/billing.py - Creem-backed Catea Pro billing integration; maps successful subscription events to local hosted-usage entitlements
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import (
    BillingCustomer,
    BillingPlan,
    BillingPrice,
    BillingSubscription,
    BillingUsagePeriod,
    BillingUsageWindow,
    BillingWebhookEvent,
)
from app.schemas import (
    BillingCheckoutRequest,
    BillingCheckoutResponse,
    BillingLicenseStatusResponse,
)


router = APIRouter(prefix="", tags=["Billing"])

ACTIVE_STATUSES = {"active", "trialing", "paid", "scheduled_cancel"}
GRANT_EVENTS = {"checkout.completed", "subscription.active", "subscription.paid", "subscription.trialing"}
REVOKE_EVENTS = {"subscription.expired", "subscription.paused"}


def _now() -> datetime:
    return datetime.utcnow()


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo:
        return parsed.replace(tzinfo=None)
    return parsed


def _normalize_email(email: str) -> str:
    return email.strip().lower()


PRO_PLAN_ID = "pro_monthly"


def _plan_for_product(product_id: Optional[str]) -> str:
    if product_id in {
        settings.creem_product_id_pro_monthly,
        settings.creem_product_id_pro_monthly_cny,
    }:
        return PRO_PLAN_ID
    if product_id == settings.creem_product_id_pro_yearly:
        # Legacy test product compatibility; new product model is monthly only.
        return PRO_PLAN_ID
    return "pro_monthly"


def _product_for_plan(plan: str, currency: str = "USD") -> str:
    if currency == "CNY" and settings.creem_product_id_pro_monthly_cny:
        return settings.creem_product_id_pro_monthly_cny
    return settings.creem_product_id_pro_monthly


def _limits_for_pro(pro: bool) -> dict[str, int]:
    return {"hosted_models": 1 if pro else 0}


async def _ensure_pro_plan(db: AsyncSession) -> BillingPlan:
    result = await db.execute(select(BillingPlan).where(BillingPlan.plan_id == PRO_PLAN_ID))
    plan = result.scalar_one_or_none()
    if not plan:
        plan = BillingPlan(plan_id=PRO_PLAN_ID, name="Pro")
        db.add(plan)
        await db.flush()
    plan.name = "Pro"
    plan.billing_period = "monthly"
    plan.monthly_credits = settings.catea_pro_monthly_credits
    plan.window_credits = settings.catea_pro_window_credits
    plan.window_hours = settings.catea_pro_window_hours
    plan.features = {"hosted_model": True, "byok": True}
    plan.active = True

    for currency, product_id in (
        ("USD", settings.creem_product_id_pro_monthly),
        ("CNY", settings.creem_product_id_pro_monthly_cny),
    ):
        if not product_id:
            continue
        existing = await db.execute(
            select(BillingPrice).where(
                BillingPrice.provider == "creem",
                BillingPrice.provider_product_id == product_id,
            )
        )
        price = existing.scalar_one_or_none()
        if not price:
            price = BillingPrice(plan_id=plan.id, provider="creem", provider_product_id=product_id)
            db.add(price)
        price.plan_id = plan.id
        price.currency = currency
        price.active = True
    await db.flush()
    return plan


def _percent(used: int, included: int) -> dict[str, Any]:
    if included <= 0:
        return {"used_percent": 0, "remaining_percent": 0}
    used_percent = min(100, round((used / included) * 100, 2))
    return {"used_percent": used_percent, "remaining_percent": max(0, round(100 - used_percent, 2))}


async def _quota_for_subscription(
    db: AsyncSession,
    customer: BillingCustomer,
    subscription: Optional[BillingSubscription],
    plan: BillingPlan,
) -> dict[str, Any]:
    now = _now()
    period_start = subscription.current_period_start if subscription and subscription.current_period_start else now
    period_end = (
        subscription.current_period_end
        if subscription and subscription.current_period_end and subscription.current_period_end > now
        else period_start + timedelta(days=30)
    )

    period_result = await db.execute(
        select(BillingUsagePeriod).where(
            BillingUsagePeriod.customer_id == customer.id,
            BillingUsagePeriod.period_start == period_start,
            BillingUsagePeriod.period_end == period_end,
        )
    )
    period = period_result.scalar_one_or_none()
    if not period:
        period = BillingUsagePeriod(
            customer_id=customer.id,
            subscription_id=subscription.id if subscription else None,
            plan_id=plan.id,
            period_start=period_start,
            period_end=period_end,
            included_credits=plan.monthly_credits,
            used_credits=0,
        )
        db.add(period)

    window_hours = max(1, plan.window_hours or 5)
    window_start = now.replace(minute=0, second=0, microsecond=0)
    hour_offset = window_start.hour % window_hours
    window_start = window_start - timedelta(hours=hour_offset)
    window_end = window_start + timedelta(hours=window_hours)
    window_result = await db.execute(
        select(BillingUsageWindow).where(
            BillingUsageWindow.customer_id == customer.id,
            BillingUsageWindow.window_start == window_start,
            BillingUsageWindow.window_end == window_end,
        )
    )
    window = window_result.scalar_one_or_none()
    if not window:
        window = BillingUsageWindow(
            customer_id=customer.id,
            plan_id=plan.id,
            window_start=window_start,
            window_end=window_end,
            included_credits=plan.window_credits,
            used_credits=0,
        )
        db.add(window)
    await db.flush()

    monthly = _percent(period.used_credits, period.included_credits)
    monthly["reset_at"] = period.period_end
    monthly["included_credits"] = period.included_credits
    monthly["used_credits"] = period.used_credits
    window_quota = _percent(window.used_credits, window.included_credits)
    window_quota["reset_at"] = window.window_end
    window_quota["included_credits"] = window.included_credits
    window_quota["used_credits"] = window.used_credits
    return {"monthly": monthly, "window": window_quota}


async def _get_or_create_customer(
    db: AsyncSession,
    email: str,
    provider_customer_id: Optional[str] = None,
) -> BillingCustomer:
    normalized_email = _normalize_email(email)
    result = await db.execute(select(BillingCustomer).where(BillingCustomer.email == normalized_email))
    customer = result.scalar_one_or_none()
    if customer:
        if provider_customer_id and customer.provider_customer_id != provider_customer_id:
            customer.provider_customer_id = provider_customer_id
            customer.updated_at = _now()
        return customer

    customer = BillingCustomer(
        email=normalized_email,
        provider="creem",
        provider_customer_id=provider_customer_id,
        license_key="catea_" + secrets.token_urlsafe(24),
    )
    db.add(customer)
    await db.flush()
    return customer


async def _get_subscription_status(db: AsyncSession, customer: BillingCustomer) -> Optional[BillingSubscription]:
    result = await db.execute(
        select(BillingSubscription)
        .where(BillingSubscription.customer_id == customer.id)
        .order_by(BillingSubscription.updated_at.desc())
    )
    return result.scalars().first()


def _verify_creem_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    if not settings.creem_webhook_secret or not signature:
        return False
    computed = hmac.new(
        settings.creem_webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


async def _upsert_subscription_from_event(
    db: AsyncSession,
    event_type: str,
    obj: dict[str, Any],
) -> None:
    if obj.get("object") == "checkout":
        checkout = obj
        subscription = checkout.get("subscription") or {}
        customer_payload = checkout.get("customer") or {}
        product_payload = checkout.get("product") or {}
        order_payload = checkout.get("order") or {}
        checkout_id = checkout.get("id")
    else:
        subscription = obj
        customer_payload = subscription.get("customer") or {}
        product_payload = subscription.get("product") or {}
        order_payload = {}
        checkout_id = None

    email = customer_payload.get("email")
    if not email:
        return

    provider_subscription_id = subscription.get("id") or checkout_id or f"checkout:{checkout_id}"
    if not provider_subscription_id:
        return

    provider_customer_id = customer_payload.get("id") if isinstance(customer_payload, dict) else None
    product_id = (
        product_payload.get("id")
        if isinstance(product_payload, dict)
        else subscription.get("product")
    )
    plan = _plan_for_product(product_id)
    current_period_start = _parse_datetime(subscription.get("current_period_start_date"))
    current_period_end = _parse_datetime(subscription.get("current_period_end_date"))
    canceled_at = _parse_datetime(subscription.get("canceled_at"))

    raw_status = subscription.get("status") or checkout.get("status") if obj.get("object") == "checkout" else subscription.get("status")
    status_value = "active" if event_type in GRANT_EVENTS and raw_status in {None, "completed"} else (raw_status or "active")
    if event_type == "subscription.scheduled_cancel":
        status_value = "scheduled_cancel"
    if event_type == "subscription.canceled":
        status_value = "canceled"

    if event_type in GRANT_EVENTS:
        active = True
    elif event_type in REVOKE_EVENTS:
        active = False
    elif event_type == "subscription.canceled":
        active = bool(current_period_end and current_period_end > _now())
    elif event_type == "subscription.scheduled_cancel":
        active = True
    else:
        active = status_value in ACTIVE_STATUSES

    customer = await _get_or_create_customer(db, email, provider_customer_id)
    result = await db.execute(
        select(BillingSubscription).where(
            BillingSubscription.provider == "creem",
            BillingSubscription.provider_subscription_id == provider_subscription_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        record = BillingSubscription(
            customer_id=customer.id,
            provider="creem",
            provider_subscription_id=provider_subscription_id,
            plan=plan,
            status=status_value,
        )
        db.add(record)

    record.customer_id = customer.id
    record.provider_order_id = order_payload.get("id") if isinstance(order_payload, dict) else None
    record.provider_checkout_id = checkout_id
    record.product_id = product_id
    record.plan = plan
    record.status = status_value
    record.active = active
    record.current_period_start = current_period_start
    record.current_period_end = current_period_end
    record.canceled_at = canceled_at
    record.provider_metadata = {
        "event_type": event_type,
        "customer": customer_payload,
        "product": product_payload,
        "metadata": obj.get("metadata") or subscription.get("metadata") or {},
    }
    record.updated_at = _now()


@router.post("/creem/checkout", response_model=BillingCheckoutResponse)
async def create_creem_checkout(
    payload: BillingCheckoutRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a Creem checkout session for Catea Pro."""
    if not settings.creem_api_key:
        raise HTTPException(status_code=500, detail="Creem API key is not configured")

    await _ensure_pro_plan(db)
    product_id = _product_for_plan(payload.plan, payload.currency)
    if not product_id:
        raise HTTPException(
            status_code=500,
            detail=f"Creem product for plan '{payload.plan}' and currency '{payload.currency}' is not configured",
        )
    checkout_currency = (
        payload.currency
        if payload.currency != "CNY" or settings.creem_product_id_pro_monthly_cny
        else "USD"
    )

    customer = await _get_or_create_customer(db, payload.email)
    success_url = payload.success_url or settings.catea_billing_success_url
    request_id = f"catea_{customer.uuid}_{int(_now().timestamp())}"

    body = {
        "product_id": product_id,
        "request_id": request_id,
        "success_url": success_url,
        "customer": {"email": customer.email},
        "metadata": {
            "referenceId": customer.uuid,
            "email": customer.email,
            "licenseKey": customer.license_key,
            "plan": PRO_PLAN_ID,
            "currency": checkout_currency,
            "requestedCurrency": payload.currency,
            "source": "catea",
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings.creem_api_base.rstrip('/')}/v1/checkouts",
            headers={
                "x-api-key": settings.creem_api_key,
                "content-type": "application/json",
            },
            json=body,
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    checkout = response.json()
    checkout_url = checkout.get("checkout_url") or checkout.get("checkoutUrl")
    checkout_id = checkout.get("id")
    if not checkout_url or not checkout_id:
        raise HTTPException(status_code=502, detail="Creem checkout response did not include checkout URL")

    return BillingCheckoutResponse(
        checkout_id=checkout_id,
        checkout_url=checkout_url,
        product_id=product_id,
        plan=PRO_PLAN_ID,
    )


@router.post("/creem/webhook")
async def creem_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Verify and process Creem webhook events."""
    raw_body = await request.body()
    signature = request.headers.get("creem-signature")
    if not _verify_creem_signature(raw_body, signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Creem signature")

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event_id = event.get("id")
    event_type = event.get("eventType")
    if not event_id or not event_type:
        raise HTTPException(status_code=400, detail="Missing Creem event id or eventType")

    result = await db.execute(
        select(BillingWebhookEvent).where(
            BillingWebhookEvent.provider == "creem",
            BillingWebhookEvent.provider_event_id == event_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing and existing.processed:
        return {"ok": True, "duplicate": True}

    event_record = existing or BillingWebhookEvent(
        provider="creem",
        provider_event_id=event_id,
        event_type=event_type,
        payload=event,
    )
    if not existing:
        db.add(event_record)

    try:
        if event_type in GRANT_EVENTS or event_type.startswith("subscription."):
            await _upsert_subscription_from_event(db, event_type, event.get("object") or {})
        elif event_type in {"refund.created", "dispute.created"}:
            # Conservative MVP behavior: record the event; manual review can
            # revoke access if needed once refund/dispute policy is finalized.
            pass
        event_record.processed = True
        event_record.processed_at = _now()
        event_record.error_message = None
    except Exception as exc:
        event_record.processed = False
        event_record.error_message = str(exc)
        raise

    return {"ok": True}


async def _status_response(
    db: AsyncSession,
    email: Optional[str] = None,
    license_key: Optional[str] = None,
) -> BillingLicenseStatusResponse:
    if not email and not license_key:
        raise HTTPException(status_code=400, detail="email or license_key is required")

    query = select(BillingCustomer)
    if license_key:
        query = query.where(BillingCustomer.license_key == license_key)
    else:
        query = query.where(BillingCustomer.email == _normalize_email(email or ""))

    result = await db.execute(query)
    customer = result.scalar_one_or_none()
    if not customer:
        return BillingLicenseStatusResponse(
            pro=False,
            email=_normalize_email(email) if email else None,
            plan="free",
            display_name="Free",
            status="not_found",
            limits=_limits_for_pro(False),
            quota=None,
            features={"hosted_model": False, "byok": True},
        )

    subscription = await _get_subscription_status(db, customer)
    pro = bool(subscription and subscription.active)
    quota = None
    display_name = "Free"
    plan_id = "free"
    if pro:
        plan = await _ensure_pro_plan(db)
        quota = await _quota_for_subscription(db, customer, subscription, plan)
        display_name = plan.name
        plan_id = plan.plan_id
    return BillingLicenseStatusResponse(
        pro=pro,
        email=customer.email,
        license_key=customer.license_key,
        plan=plan_id,
        display_name=display_name,
        status=subscription.status if subscription else "no_subscription",
        current_period_end=subscription.current_period_end if subscription else None,
        limits=_limits_for_pro(pro),
        quota=quota,
        features={"hosted_model": pro, "byok": True},
    )


@router.get("/me", response_model=BillingLicenseStatusResponse)
async def billing_me(
    email: Optional[str] = Query(default=None),
    license_key: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Return the user's Catea plan, entitlement, and quota status."""
    return await _status_response(db, email=email, license_key=license_key)


@router.get("/license/status", response_model=BillingLicenseStatusResponse)
async def license_status(
    email: Optional[str] = Query(default=None),
    license_key: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Return Catea Pro entitlement status for an email or license key."""
    return await _status_response(db, email=email, license_key=license_key)


@router.get("/success", response_class=HTMLResponse)
async def billing_success():
    """Simple success page for hosted checkout redirects."""
    return """
    <!doctype html>
    <html>
      <head><title>Catea Pro checkout complete</title></head>
      <body style="font-family: system-ui, sans-serif; max-width: 720px; margin: 48px auto;">
        <h1>Catea Pro checkout complete</h1>
        <p>Your payment was received by Creem. You can return to Catea and refresh your Pro status.</p>
      </body>
    </html>
    """
