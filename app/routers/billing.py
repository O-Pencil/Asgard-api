"""
[WHO]: Provides billing router for checkout creation, webhook fulfillment, license status lookup, hosted model proxy, and payment success response
[FROM]: Depends on FastAPI request handling, SQLAlchemy async sessions, httpx for payment and hosted model API calls, hmac/hashlib and cryptography for webhook/signature verification, app.config settings, app.models billing tables, app.schemas billing DTOs
[TO]: Consumed by main.py as /billing routes for Catea Pro payment and entitlement testing
[HERE]: packages/api/app/routers/billing.py - Payment-backed Catea Pro billing integration; maps successful subscription events to local hosted-usage entitlements and enforces hosted model quota
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_password_hash
from app.config import settings
from app.database import get_db
from app.models import (
    BillingCustomer,
    BillingPlan,
    BillingPrice,
    BillingSubscription,
    BillingUsageEvent,
    BillingUsagePeriod,
    BillingUsageWindow,
    BillingWebhookEvent,
    User,
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


def _bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


WAFFO_CHECKOUT_PATH = "/v1/actions/checkout/create-session"
WAFFO_GRANT_EVENTS = {
    "order.completed",
    "payment.completed",
    "subscription.active",
    "subscription.activated",
    "subscription.payment_succeeded",
    "subscription.paid",
}
WAFFO_REVOKE_EVENTS = {
    "subscription.canceled",
    "subscription.cancelled",
    "subscription.expired",
    "subscription.paused",
    "subscription.past_due",
}


PRO_PLAN_ID = "pro_monthly"


def _plan_for_product(product_id: Optional[str]) -> str:
    if product_id in {
        settings.creem_product_id_pro_monthly,
        settings.creem_product_id_pro_monthly_cny,
        settings.waffo_product_id_pro_monthly,
    }:
        return PRO_PLAN_ID
    if product_id == settings.creem_product_id_pro_yearly:
        # Legacy test product compatibility; new product model is monthly only.
        return PRO_PLAN_ID
    return "pro_monthly"


def _product_for_plan(plan: str, currency: str = "USD") -> str:
    if settings.billing_provider.lower() == "waffo":
        return settings.waffo_product_id_pro_monthly
    if currency == "CNY" and settings.creem_product_id_pro_monthly_cny:
        return settings.creem_product_id_pro_monthly_cny
    return settings.creem_product_id_pro_monthly


def _creem_product_for_plan(plan: str, currency: str = "USD") -> str:
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
    if settings.waffo_product_id_pro_monthly:
        existing = await db.execute(
            select(BillingPrice).where(
                BillingPrice.provider == "waffo",
                BillingPrice.provider_product_id == settings.waffo_product_id_pro_monthly,
            )
        )
        price = existing.scalar_one_or_none()
        if not price:
            price = BillingPrice(
                plan_id=plan.id,
                provider="waffo",
                provider_product_id=settings.waffo_product_id_pro_monthly,
            )
            db.add(price)
        price.plan_id = plan.id
        price.currency = "USD"
        price.active = True
    await db.flush()
    return plan


async def _ensure_billing_user(db: AsyncSession, email: str) -> User:
    normalized_email = _normalize_email(email)
    result = await db.execute(select(User).where(User.email == normalized_email))
    user = result.scalar_one_or_none()
    if user:
        return user
    user = User(
        email=normalized_email,
        hashed_password=get_password_hash(secrets.token_urlsafe(32)),
        full_name="Catea billing user",
        balance=0.0,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


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
    period, window = await _ensure_usage_buckets(db, customer, subscription, plan)
    monthly = _percent(period.used_credits, period.included_credits)
    monthly["reset_at"] = period.period_end
    monthly["included_credits"] = period.included_credits
    monthly["used_credits"] = period.used_credits
    window_quota = _percent(window.used_credits, window.included_credits)
    window_quota["reset_at"] = window.window_end
    window_quota["included_credits"] = window.included_credits
    window_quota["used_credits"] = window.used_credits
    return {"monthly": monthly, "window": window_quota}


async def _ensure_usage_buckets(
    db: AsyncSession,
    customer: BillingCustomer,
    subscription: Optional[BillingSubscription],
    plan: BillingPlan,
) -> tuple[BillingUsagePeriod, BillingUsageWindow]:
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
    return period, window


async def _get_or_create_customer(
    db: AsyncSession,
    email: str,
    provider_customer_id: Optional[str] = None,
    provider: str = "creem",
) -> BillingCustomer:
    normalized_email = _normalize_email(email)
    result = await db.execute(select(BillingCustomer).where(BillingCustomer.email == normalized_email))
    customer = result.scalar_one_or_none()
    if customer:
        if provider and customer.provider != provider:
            customer.provider = provider
        if provider_customer_id and customer.provider_customer_id != provider_customer_id:
            customer.provider_customer_id = provider_customer_id
            customer.updated_at = _now()
        return customer

    customer = BillingCustomer(
        email=normalized_email,
        provider=provider,
        provider_customer_id=provider_customer_id,
        license_key="catea_" + secrets.token_urlsafe(24),
    )
    db.add(customer)
    await _ensure_billing_user(db, normalized_email)
    await db.flush()
    return customer


async def _get_subscription_status(db: AsyncSession, customer: BillingCustomer) -> Optional[BillingSubscription]:
    result = await db.execute(
        select(BillingSubscription)
        .where(BillingSubscription.customer_id == customer.id)
        .order_by(BillingSubscription.updated_at.desc())
    )
    subscription = result.scalars().first()
    if (
        subscription
        and subscription.active
        and subscription.current_period_end
        and subscription.current_period_end <= _now()
    ):
        subscription.active = False
        subscription.status = "expired"
        db.add(subscription)
        await db.flush()
    return subscription


async def _get_active_hosted_customer(
    db: AsyncSession,
    license_key: str,
) -> tuple[BillingCustomer, BillingSubscription, BillingPlan, BillingUsagePeriod, BillingUsageWindow]:
    result = await db.execute(select(BillingCustomer).where(BillingCustomer.license_key == license_key))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Catea Pro license")
    subscription = await _get_subscription_status(db, customer)
    if not subscription or not subscription.active:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="Catea Pro subscription is required")
    plan = await _ensure_pro_plan(db)
    period, window = await _ensure_usage_buckets(db, customer, subscription, plan)
    if period.used_credits >= period.included_credits:
        raise HTTPException(status_code=429, detail="Monthly hosted model quota exceeded")
    if window.used_credits >= window.included_credits:
        raise HTTPException(status_code=429, detail="Hosted model quota will reset soon")
    return customer, subscription, plan, period, window


def _message_text(message: Any) -> str:
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts)
        return ""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _estimate_tokens_from_messages(messages: list[Any]) -> int:
    text = "\n".join(_message_text(message) for message in messages)
    return max(1, len(text) // 4)


def _usage_tokens(payload: dict[str, Any], fallback: int) -> tuple[int, int, int]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return fallback, 0, fallback
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or prompt + completion or fallback)
    return prompt or fallback, completion, total


async def _record_hosted_usage(
    db: AsyncSession,
    customer: BillingCustomer,
    period: BillingUsagePeriod,
    window: BillingUsageWindow,
    request_id: str,
    model_route: str,
    input_tokens: int,
    output_tokens: int,
    credits: int,
) -> None:
    safe_credits = max(1, credits)
    event = BillingUsageEvent(
        customer_id=customer.id,
        request_id=request_id,
        model_route=model_route,
        input_tokens=max(0, input_tokens),
        output_tokens=max(0, output_tokens),
        credits=safe_credits,
    )
    period.used_credits = min(period.included_credits, period.used_credits + safe_credits)
    window.used_credits = min(window.included_credits, window.used_credits + safe_credits)
    db.add(event)
    db.add(period)
    db.add(window)
    await db.commit()


def _hosted_model_url() -> str:
    return f"{settings.catea_hosted_model_base_url.rstrip('/')}/chat/completions"


def _hosted_model_body(request: dict[str, Any]) -> dict[str, Any]:
    body = dict(request)
    body["model"] = settings.catea_hosted_model_name
    if settings.catea_hosted_model_reasoning_effort:
        body.setdefault("reasoning_effort", settings.catea_hosted_model_reasoning_effort)
    return body


def _verify_creem_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    if not settings.creem_webhook_secret or not signature:
        return False
    computed = hmac.new(
        settings.creem_webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


WAFFO_TEST_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAxnmRY6yMMA3lVqmAU6ZG
b1sjL/+r/z6E+ZjkXaDAKiqOhk9rpazni0bNsGXwmftTPk9jy2wn+j6JHODD/WH/
SCnSfvKkLIjy4Hk7BuCgB174C0ydan7J+KgXLkOwgCAxxB68t2tezldwo74ZpXgn
F49opzMvQ9prEwIAWOE+kV9iK6gx/AckSMtHIHpUesoPDkldpmFHlB2qpf1vsFTZ
5kD6DmGl+2GIVK01aChy2lk8pLv0yUMu18v44sLkO5M44TkGPJD9qG09wrvVG2wp
OTVCn1n5pP8P+HRLcgzbUB3OlZVfdFurn6EZwtyL4ZD9kdkQ4EZE/9inKcp3c1h4
xwIDAQAB
-----END PUBLIC KEY-----"""

WAFFO_PROD_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAz+xApdTIb4ua+DgZKQ54
iBsD82ybyhGCLRETONW4Jgbb3A8DUM1LqBk6r/CmTOCHqLalTQHNigvP3R5zkDNX
iRJz6gA4MJ/+8K0+mnEE2RISQzN+Qu65TNd6svb+INm/kMaftY4uIXr6y6kchtTJ
dwnQhcKdAL2v7h7IFnkVelQsKxDdb2PqX8xX/qwd01iXvMcpCCaXovUwZsxH2QN5
ZKBTseJivbhUeyJCco4fdUyxOMHe2ybCVhyvim2uxAl1nkvL5L8RCWMCAV55LLo0
9OhmLahz/DYNu13YLVP6dvIT09ZFBYU6Owj1NxdinTynlJCFS9VYwBgmftosSE1U
dwIDAQAB
-----END PUBLIC KEY-----"""


def _wrap_base64_key(raw: str, header: str, footer: str) -> str:
    base64_body = "".join(raw.split())
    lines = "\n".join(base64_body[index : index + 64] for index in range(0, len(base64_body), 64))
    return f"{header}\n{lines}\n{footer}"


def _normalize_waffo_private_key(raw: str) -> bytes:
    value = raw.replace("\\n", "\n").replace("\r\n", "\n").strip()
    if "-----BEGIN" in value:
        return value.encode("utf-8")
    pem = _wrap_base64_key(value, "-----BEGIN PRIVATE KEY-----", "-----END PRIVATE KEY-----")
    return pem.encode("utf-8")


def _normalize_waffo_public_key(raw: str) -> bytes:
    value = raw.replace("\\n", "\n").replace("\r\n", "\n").strip()
    if "-----BEGIN" in value:
        return value.encode("utf-8")
    pem = _wrap_base64_key(value, "-----BEGIN PUBLIC KEY-----", "-----END PUBLIC KEY-----")
    return pem.encode("utf-8")


def _waffo_private_key():
    return serialization.load_pem_private_key(
        _normalize_waffo_private_key(settings.waffo_private_key),
        password=None,
    )


def _waffo_public_key(environment: str):
    configured = settings.waffo_webhook_public_key.strip()
    if configured:
        key = configured
    elif environment == "prod":
        key = WAFFO_PROD_PUBLIC_KEY
    else:
        key = WAFFO_TEST_PUBLIC_KEY
    return serialization.load_pem_public_key(_normalize_waffo_public_key(key))


def _waffo_body_json(body: dict[str, Any]) -> str:
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


def _waffo_signature(method: str, path: str, timestamp: str, body_json: str) -> str:
    body_hash = base64.b64encode(hashlib.sha256(body_json.encode("utf-8")).digest()).decode("ascii")
    canonical_request = f"{method}\n{path}\n{timestamp}\n{body_hash}"
    signature = _waffo_private_key().sign(
        canonical_request.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def _verify_waffo_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    if not signature_header:
        return False
    pairs = {}
    for pair in signature_header.split(","):
        key, separator, value = pair.partition("=")
        if separator:
            pairs[key.strip()] = value.strip()
    timestamp = pairs.get("t")
    signature = pairs.get("v1")
    if not timestamp or not signature:
        return False
    try:
        age_ms = int(_now().timestamp() * 1000) - int(timestamp)
    except ValueError:
        return False
    if age_ms > 45 * 60 * 1000 or age_ms < -60 * 1000:
        return False
    public_key = _waffo_public_key(settings.waffo_environment.lower())
    try:
        public_key.verify(
            base64.b64decode(signature),
            timestamp.encode("utf-8") + b"." + raw_body,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception:
        return False
    return True


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


def _first_string(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _waffo_event_period(data: dict[str, Any]) -> tuple[Optional[datetime], Optional[datetime]]:
    start = _parse_datetime(
        _first_string(
            data.get("currentPeriodStart"),
            data.get("current_period_start"),
            data.get("current_period_start_date"),
            data.get("periodStart"),
            data.get("period_start"),
        )
    )
    end = _parse_datetime(
        _first_string(
            data.get("currentPeriodEnd"),
            data.get("current_period_end"),
            data.get("current_period_end_date"),
            data.get("periodEnd"),
            data.get("period_end"),
            data.get("nextBillingAt"),
            data.get("next_billing_at"),
        )
    )
    if not end and start:
        end = start + timedelta(days=30)
    if not start:
        start = _parse_datetime(_first_string(data.get("createdAt"), data.get("created_at"))) or _now()
    if not end:
        end = start + timedelta(days=30)
    return start, end


async def _upsert_waffo_subscription_from_event(
    db: AsyncSession,
    event: dict[str, Any],
) -> None:
    event_type = str(event.get("eventType") or "")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    metadata = data.get("orderMetadata") if isinstance(data.get("orderMetadata"), dict) else {}
    email = _first_string(
        data.get("buyerEmail"),
        data.get("customerEmail"),
        metadata.get("email"),
    )
    if not email:
        return

    order_id = _first_string(data.get("orderId"), data.get("id"), event.get("eventId"))
    subscription_id = _first_string(
        data.get("subscriptionId"),
        data.get("subscriptionOrderId"),
        data.get("originOrderId"),
        order_id,
    )
    if not subscription_id:
        return

    product_id = _first_string(
        data.get("productId"),
        metadata.get("productId"),
        settings.waffo_product_id_pro_monthly,
    )
    raw_status = _first_string(data.get("status"), data.get("subscriptionStatus"))
    if event_type in WAFFO_GRANT_EVENTS:
        status_value = raw_status or "active"
        active = True
    elif event_type in WAFFO_REVOKE_EVENTS:
        status_value = raw_status or event_type.removeprefix("subscription.")
        active = status_value in {"canceling", "scheduled_cancel"}
    else:
        status_value = raw_status or "active"
        active = status_value in ACTIVE_STATUSES

    current_period_start, current_period_end = _waffo_event_period(data)
    canceled_at = _parse_datetime(_first_string(data.get("canceledAt"), data.get("cancelledAt"), data.get("canceled_at")))
    customer = await _get_or_create_customer(db, email, provider_customer_id=None, provider="waffo")
    result = await db.execute(
        select(BillingSubscription).where(
            BillingSubscription.provider == "waffo",
            BillingSubscription.provider_subscription_id == subscription_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        record = BillingSubscription(
            customer_id=customer.id,
            provider="waffo",
            provider_subscription_id=subscription_id,
            plan=_plan_for_product(product_id),
            status=status_value,
        )
        db.add(record)

    record.customer_id = customer.id
    record.provider_order_id = order_id
    record.provider_checkout_id = _first_string(data.get("checkoutSessionId"), data.get("checkoutId"))
    record.product_id = product_id
    record.plan = _plan_for_product(product_id)
    record.status = status_value
    record.active = active
    record.current_period_start = current_period_start
    record.current_period_end = current_period_end
    record.canceled_at = canceled_at
    record.provider_metadata = {"event": event, "metadata": metadata}
    record.updated_at = _now()


async def _create_waffo_checkout(
    payload: BillingCheckoutRequest,
    db: AsyncSession,
) -> BillingCheckoutResponse:
    """Create a Waffo Pancake checkout session for Catea Pro."""
    if not settings.waffo_merchant_id or not settings.waffo_private_key:
        raise HTTPException(status_code=500, detail="Waffo Pancake credentials are not configured")
    if not settings.waffo_store_id:
        raise HTTPException(status_code=500, detail="Waffo Pancake store is not configured")

    await _ensure_pro_plan(db)
    product_id = _product_for_plan(payload.plan, payload.currency)
    if not product_id:
        raise HTTPException(status_code=500, detail=f"Waffo product for plan '{payload.plan}' is not configured")
    if payload.currency == "CNY":
        raise HTTPException(status_code=400, detail="CNY subscriptions are not supported in the current Waffo test setup")

    customer = await _get_or_create_customer(db, payload.email, provider="waffo")
    await _ensure_billing_user(db, customer.email)
    success_url = payload.success_url or settings.catea_billing_success_url
    request_id = f"catea_{customer.uuid}_{int(_now().timestamp())}"
    body = {
        "storeId": settings.waffo_store_id,
        "productId": product_id,
        "productType": "subscription",
        "currency": "USD",
        "buyerEmail": customer.email,
        "successUrl": success_url,
        "metadata": {
            "referenceId": customer.uuid,
            "email": customer.email,
            "licenseKey": customer.license_key,
            "plan": PRO_PLAN_ID,
            "currency": "USD",
            "requestedCurrency": payload.currency,
            "productId": product_id,
            "source": "catea",
        },
        "orderMerchantExternalId": request_id,
    }
    body_json = _waffo_body_json(body)
    timestamp = str(int(_now().timestamp()))
    signature = _waffo_signature("POST", WAFFO_CHECKOUT_PATH, timestamp, body_json)

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings.waffo_api_base.rstrip('/')}{WAFFO_CHECKOUT_PATH}",
            headers={
                "Content-Type": "application/json",
                "X-Merchant-Id": settings.waffo_merchant_id,
                "X-Timestamp": timestamp,
                "X-Signature": signature,
                "X-Idempotency-Key": request_id,
            },
            content=body_json,
        )
    try:
        checkout = response.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail=response.text)
    if response.status_code >= 400 or checkout.get("errors"):
        raise HTTPException(status_code=response.status_code, detail=checkout)

    data = checkout.get("data") if isinstance(checkout.get("data"), dict) else checkout
    checkout_url = data.get("checkoutUrl") or data.get("checkout_url")
    checkout_id = data.get("sessionId") or data.get("checkoutSessionId") or data.get("id")
    if not checkout_url or not checkout_id:
        raise HTTPException(status_code=502, detail="Waffo checkout response did not include checkout URL")

    return BillingCheckoutResponse(
        checkout_id=checkout_id,
        checkout_url=checkout_url,
        product_id=product_id,
        plan=PRO_PLAN_ID,
    )


async def _create_creem_checkout(
    payload: BillingCheckoutRequest,
    db: AsyncSession,
) -> BillingCheckoutResponse:
    """Create a Creem checkout session for Catea Pro."""
    if not settings.creem_api_key:
        raise HTTPException(status_code=500, detail="Creem API key is not configured")

    await _ensure_pro_plan(db)
    product_id = _creem_product_for_plan(payload.plan, payload.currency)
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
    await _ensure_billing_user(db, customer.email)
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


@router.post("/checkout", response_model=BillingCheckoutResponse)
async def create_billing_checkout(
    payload: BillingCheckoutRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a checkout session with the configured payment provider."""
    if settings.billing_provider.lower() == "waffo":
        return await _create_waffo_checkout(payload, db)
    return await _create_creem_checkout(payload, db)


@router.post("/waffo/checkout", response_model=BillingCheckoutResponse)
async def create_waffo_checkout(
    payload: BillingCheckoutRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a Waffo Pancake checkout session for Catea Pro."""
    return await _create_waffo_checkout(payload, db)


@router.post("/creem/checkout", response_model=BillingCheckoutResponse)
async def create_creem_checkout(
    payload: BillingCheckoutRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a Creem checkout session for backward compatibility."""
    if settings.billing_provider.lower() == "waffo":
        return await _create_waffo_checkout(payload, db)
    return await _create_creem_checkout(payload, db)


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


@router.post("/waffo/webhook")
async def waffo_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Verify and process Waffo Pancake webhook events."""
    raw_body = await request.body()
    signature = request.headers.get("x-waffo-signature") or request.headers.get("X-Waffo-Signature")
    if not _verify_waffo_signature(raw_body, signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Waffo signature")

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event_id = event.get("id") or event.get("eventId")
    event_type = event.get("eventType")
    if not event_id or not event_type:
        raise HTTPException(status_code=400, detail="Missing Waffo event id or eventType")

    result = await db.execute(
        select(BillingWebhookEvent).where(
            BillingWebhookEvent.provider == "waffo",
            BillingWebhookEvent.provider_event_id == str(event_id),
        )
    )
    existing = result.scalar_one_or_none()
    if existing and existing.processed:
        return {"ok": True, "duplicate": True}

    event_record = existing or BillingWebhookEvent(
        provider="waffo",
        provider_event_id=str(event_id),
        event_type=str(event_type),
        payload=event,
    )
    if not existing:
        db.add(event_record)

    try:
        if str(event_type) in WAFFO_GRANT_EVENTS or str(event_type).startswith("subscription."):
            await _upsert_waffo_subscription_from_event(db, event)
        elif str(event_type) in {"refund.created", "dispute.created", "order.refunded"}:
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
    if email:
        await _ensure_billing_user(db, email)
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


@router.post("/hosted/v1/chat/completions")
async def hosted_chat_completions(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_catea_license: Optional[str] = Header(default=None, alias="X-Catea-License"),
    db: AsyncSession = Depends(get_db),
):
    """OpenAI-compatible hosted model endpoint for Catea Pro users."""
    if not settings.catea_hosted_model_api_key:
        raise HTTPException(status_code=503, detail="Catea hosted model is not configured")
    license_key = (x_catea_license or "").strip() or _bearer_token(authorization)
    if not license_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Catea Pro license is required")

    customer, _, _, period, window = await _get_active_hosted_customer(db, license_key)
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="messages must be an array")
    request_id = "hosted_" + secrets.token_urlsafe(24)
    prompt_tokens = _estimate_tokens_from_messages(messages)
    outbound_body = _hosted_model_body(payload)
    headers = {
        "Authorization": f"Bearer {settings.catea_hosted_model_api_key}",
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(settings.catea_hosted_model_timeout_s, connect=20.0)
    if payload.get("stream") is True:
        async def generate_stream():
            output_chars = 0
            completed = False
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    _hosted_model_url(),
                    headers=headers,
                    json=outbound_body,
                ) as response:
                    if response.status_code >= 400:
                        text = await response.aread()
                        raise HTTPException(status_code=response.status_code, detail=text.decode("utf-8", "ignore"))
                    async for line in response.aiter_lines():
                        if not line:
                            yield b"\n"
                            continue
                        if line.startswith("data:"):
                            data = line[5:].strip()
                            if data == "[DONE]":
                                completed = True
                            else:
                                try:
                                    chunk = json.loads(data)
                                    delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                                    content = delta.get("content")
                                    if isinstance(content, str):
                                        output_chars += len(content)
                                except (json.JSONDecodeError, AttributeError, IndexError, TypeError):
                                    pass
                        yield f"{line}\n\n".encode("utf-8")
                        await asyncio.sleep(0)
            if completed:
                output_tokens = max(0, output_chars // 4)
                await _record_hosted_usage(
                    db=db,
                    customer=customer,
                    period=period,
                    window=window,
                    request_id=request_id,
                    model_route=settings.catea_hosted_model_name,
                    input_tokens=prompt_tokens,
                    output_tokens=output_tokens,
                    credits=prompt_tokens + output_tokens,
                )

        return StreamingResponse(generate_stream(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(_hosted_model_url(), headers=headers, json=outbound_body)
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    body = response.json()
    input_tokens, output_tokens, total_tokens = _usage_tokens(body, prompt_tokens)
    await _record_hosted_usage(
        db=db,
        customer=customer,
        period=period,
        window=window,
        request_id=request_id,
        model_route=settings.catea_hosted_model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        credits=total_tokens,
    )
    return body


@router.get("/success", response_class=HTMLResponse)
async def billing_success():
    """Simple success page for hosted checkout redirects."""
    return """
    <!doctype html>
    <html>
      <head><title>Catea Pro checkout complete</title></head>
      <body style="font-family: system-ui, sans-serif; max-width: 720px; margin: 48px auto;">
        <h1>Catea Pro checkout complete</h1>
        <p>Your payment was received. You can return to Catea and refresh your Pro status.</p>
      </body>
    </html>
    """
