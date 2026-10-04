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
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_password_hash
from app.config import settings
from app.database import get_db
from app.models import (
    BillingCreditBalance,
    BillingCreditGrant,
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
logger = logging.getLogger(__name__)

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
WAFFO_GRAPHQL_PATH = "/v1/graphql"
WAFFO_CONTENT_SAFETY_PATH = "/v1/actions/verification/scan-prompt"
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
CREDIT_PACKS = {
    "credits_20k": {"credits": 20_000, "usd": "3.00", "cny": "18.00"},
    "credits_50k": {"credits": 50_000, "usd": "6.00", "cny": "36.00"},
    "credits_100k": {"credits": 100_000, "usd": "9.90", "cny": "60.00"},
}

HOSTED_REQUEST_ID_HEADER = "X-Catea-Request-Id"


def _hosted_error_response(
    request_id: str,
    status_code: int,
    code: str,
    message: str,
    *,
    retry_after: Optional[str] = None,
) -> JSONResponse:
    """Return a stable OpenAI-compatible error without exposing upstream details."""
    headers = {HOSTED_REQUEST_ID_HEADER: request_id}
    if retry_after:
        headers["Retry-After"] = retry_after
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={
            "error": {
                "message": message,
                "type": "catea_hosted_error",
                "code": code,
                "request_id": request_id,
            }
        },
    )


def _hosted_http_error_response(request_id: str, exc: HTTPException) -> JSONResponse:
    """Translate entitlement and safety failures into the hosted API contract."""
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    detail_code = detail.get("code") if isinstance(detail.get("code"), str) else ""
    detail_message = detail.get("message") if isinstance(detail.get("message"), str) else ""
    if detail_code.startswith("content_safety_"):
        return _hosted_error_response(
            request_id,
            exc.status_code,
            detail_code,
            detail_message or "This request could not be processed under Catea's AI usage policy.",
        )
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        return _hosted_error_response(
            request_id,
            status.HTTP_401_UNAUTHORIZED,
            "authorization_failed",
            "Catea Pro authorization failed. Refresh your plan status and try again.",
        )
    if exc.status_code == status.HTTP_402_PAYMENT_REQUIRED:
        return _hosted_error_response(
            request_id,
            status.HTTP_402_PAYMENT_REQUIRED,
            "subscription_required",
            "An active Catea Pro subscription is required.",
        )
    if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        raw_detail = str(exc.detail).lower()
        code = "window_quota_exceeded" if "reset soon" in raw_detail else "quota_exceeded"
        return _hosted_error_response(
            request_id,
            status.HTTP_429_TOO_MANY_REQUESTS,
            code,
            "Catea Pro usage is temporarily unavailable. Check your plan usage and try again later.",
        )
    if exc.status_code in {status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_ENTITY}:
        return _hosted_error_response(
            request_id,
            exc.status_code,
            detail_code or "invalid_request",
            detail_message or "The model request is invalid.",
        )
    return _hosted_error_response(
        request_id,
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail_code or "service_unavailable",
        "Catea's hosted model is temporarily unavailable. Please try again later.",
    )


def _hosted_upstream_error_response(
    request_id: str,
    upstream_status: int,
    retry_after: Optional[str] = None,
) -> JSONResponse:
    """Map provider failures to public Catea errors while retaining status in logs."""
    if upstream_status == status.HTTP_429_TOO_MANY_REQUESTS:
        return _hosted_error_response(
            request_id,
            status.HTTP_429_TOO_MANY_REQUESTS,
            "upstream_rate_limited",
            "Catea's hosted model is busy. Please try again shortly.",
            retry_after=retry_after,
        )
    if upstream_status in {status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_ENTITY}:
        return _hosted_error_response(
            request_id,
            status.HTTP_400_BAD_REQUEST,
            "upstream_rejected_request",
            "The hosted model could not process this request.",
        )
    return _hosted_error_response(
        request_id,
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "upstream_unavailable",
        "Catea's hosted model is temporarily unavailable. Please try again later.",
    )


def _credit_product_ids() -> dict[str, str]:
    return {
        "credits_20k": settings.waffo_product_id_credits_20k,
        "credits_50k": settings.waffo_product_id_credits_50k,
        "credits_100k": settings.waffo_product_id_credits_100k,
    }


def _credit_pack_for_plan(plan: str) -> Optional[dict[str, Any]]:
    return CREDIT_PACKS.get(plan)


def _credit_pack_for_product(product_id: Optional[str]) -> Optional[tuple[str, dict[str, Any]]]:
    if not product_id:
        return None
    for plan, configured_product_id in _credit_product_ids().items():
        if configured_product_id and product_id == configured_product_id:
            return plan, CREDIT_PACKS[plan]
    return None



def _plan_for_product(product_id: Optional[str]) -> str:
    if product_id in {
        settings.creem_product_id_pro_monthly,
        settings.creem_product_id_pro_monthly_cny,
        settings.waffo_product_id_pro_monthly,
        settings.waffo_product_id_pro_30d,
        _xorpay_product_id(),
    }:
        return PRO_PLAN_ID
    if product_id == settings.creem_product_id_pro_yearly:
        # Legacy test product compatibility; new product model is monthly only.
        return PRO_PLAN_ID
    return "pro_monthly"


def _product_for_plan(plan: str, currency: str = "USD") -> str:
    if settings.billing_provider.lower() == "xorpay":
        return _xorpay_product_id()
    if settings.billing_provider.lower() == "waffo":
        credit_product = _credit_product_ids().get(plan)
        if credit_product:
            return credit_product
        if plan == "pro_30d" or currency == "CNY":
            return settings.waffo_product_id_pro_30d
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


def _xorpay_product_id() -> str:
    return "xorpay_catea_pro_monthly_cny"


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
        price = await _get_or_create_billing_price(db, "creem", product_id)
        price.plan_id = plan.id
        price.currency = currency
        price.active = True
    for currency, product_id in (
        ("USD", settings.waffo_product_id_pro_monthly),
        ("CNY", settings.waffo_product_id_pro_30d),
    ):
        if not product_id:
            continue
        price = await _get_or_create_billing_price(db, "waffo", product_id)
        price.plan_id = plan.id
        price.currency = currency
        price.active = True
    for plan_id, product_id in _credit_product_ids().items():
        if not product_id:
            continue
        pack = CREDIT_PACKS[plan_id]
        price = await _get_or_create_billing_price(db, "waffo", product_id)
        price.plan_id = plan.id
        price.currency = "USD"
        price.amount = int(round(float(pack["usd"]) * 100))
        price.active = True

    if settings.xorpay_aid:
        product_id = _xorpay_product_id()
        price = await _get_or_create_billing_price(db, "xorpay", product_id)
        price.plan_id = plan.id
        price.currency = "CNY"
        try:
            price.amount = int(round(float(settings.xorpay_pro_monthly_price_cny) * 100))
        except ValueError:
            price.amount = 1800
        price.active = True
    await db.flush()
    return plan


async def _ensure_billing_user(db: AsyncSession, email: str) -> User:
    normalized_email = _normalize_email(email)
    result = await db.execute(select(User).where(User.email == normalized_email))
    user = result.scalar_one_or_none()
    if user:
        return user
    try:
        async with db.begin_nested():
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
    except IntegrityError:
        result = await db.execute(select(User).where(User.email == normalized_email))
        user = result.scalar_one_or_none()
        if user:
            return user
        raise


async def _get_or_create_billing_price(
    db: AsyncSession,
    provider: str,
    product_id: str,
) -> BillingPrice:
    result = await db.execute(
        select(BillingPrice).where(
            BillingPrice.provider == provider,
            BillingPrice.provider_product_id == product_id,
        )
    )
    price = result.scalar_one_or_none()
    if price:
        return price
    try:
        async with db.begin_nested():
            price = BillingPrice(provider=provider, provider_product_id=product_id)
            db.add(price)
            await db.flush()
            return price
    except IntegrityError:
        result = await db.execute(
            select(BillingPrice).where(
                BillingPrice.provider == provider,
                BillingPrice.provider_product_id == product_id,
            )
        )
        price = result.scalar_one_or_none()
        if price:
            return price
        raise


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
    topup_balance = await _get_credit_balance(db, customer)
    topup_granted = await db.scalar(
        select(func.coalesce(func.sum(BillingCreditGrant.credits), 0)).where(
            BillingCreditGrant.customer_id == customer.id
        )
    )
    topup_included = int(topup_granted or 0)
    topup_remaining = max(0, int(topup_balance.balance_credits or 0))
    topup_used = max(0, topup_included - topup_remaining)
    monthly = _percent(period.used_credits, period.included_credits)
    monthly["reset_at"] = period.period_end
    monthly["included_credits"] = period.included_credits
    monthly["used_credits"] = period.used_credits
    window_quota = _percent(window.used_credits, window.included_credits)
    window_quota["reset_at"] = window.window_end
    window_quota["included_credits"] = window.included_credits
    window_quota["used_credits"] = window.used_credits
    topup = _percent(topup_used, topup_included)
    topup["included_credits"] = topup_included
    topup["used_credits"] = topup_used
    topup["balance_credits"] = topup_remaining
    return {"monthly": monthly, "window": window_quota, "topup": topup}


async def _get_credit_balance(db: AsyncSession, customer: BillingCustomer) -> BillingCreditBalance:
    result = await db.execute(
        select(BillingCreditBalance).where(BillingCreditBalance.customer_id == customer.id)
    )
    balance = result.scalar_one_or_none()
    if not balance:
        balance = BillingCreditBalance(customer_id=customer.id, balance_credits=0)
        db.add(balance)
        await db.flush()
    return balance


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

    await _ensure_billing_user(db, normalized_email)
    try:
        async with db.begin_nested():
            customer = BillingCustomer(
                email=normalized_email,
                provider=provider,
                provider_customer_id=provider_customer_id,
                license_key="catea_" + secrets.token_urlsafe(24),
            )
            db.add(customer)
            await db.flush()
            return customer
    except IntegrityError:
        result = await db.execute(select(BillingCustomer).where(BillingCustomer.email == normalized_email))
        customer = result.scalar_one_or_none()
        if customer:
            if provider and customer.provider != provider:
                customer.provider = provider
            if provider_customer_id and customer.provider_customer_id != provider_customer_id:
                customer.provider_customer_id = provider_customer_id
                customer.updated_at = _now()
            return customer
        raise


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
    credit_balance = await _get_credit_balance(db, customer)
    if period.used_credits >= period.included_credits and credit_balance.balance_credits <= 0:
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


def _latest_user_prompt(messages: list[Any]) -> str:
    for message in reversed(messages):
        role = message.get("role") if isinstance(message, dict) else getattr(message, "role", None)
        if role == "user":
            return _message_text(message).strip()
    for message in reversed(messages):
        text = _message_text(message).strip()
        if text:
            return text
    return ""


def _content_safety_locale(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata")
    value = None
    if isinstance(metadata, dict):
        value = metadata.get("locale") or metadata.get("language")
    if not isinstance(value, str) or value not in {"ja", "en", "zh"}:
        value = settings.waffo_content_safety_locale
    if value not in {"ja", "en", "zh"}:
        return "zh"
    return value


def _content_safety_semantic() -> str:
    value = settings.waffo_content_safety_semantic
    if value not in {"off", "shadow", "enforce"}:
        return "enforce"
    return value


async def _scan_waffo_prompt(prompt: str, locale: str) -> dict[str, Any]:
    if not settings.waffo_content_safety_enabled:
        return {"action": "allow", "reasonCode": "disabled"}
    prompt = prompt.strip()
    if not prompt:
        return {"action": "allow", "reasonCode": "empty_prompt"}
    if not settings.waffo_merchant_id or not settings.waffo_private_key:
        raise HTTPException(status_code=503, detail="Waffo content safety is not configured")

    body = {
        "prompt": prompt[:10000],
        "locale": locale,
        "semantic": _content_safety_semantic(),
    }
    body_json = _waffo_body_json(body)
    timestamp = str(int(_now().timestamp()))
    signature = _waffo_signature("POST", WAFFO_CONTENT_SAFETY_PATH, timestamp, body_json)
    headers = {
        "Content-Type": "application/json",
        "X-Merchant-Id": settings.waffo_merchant_id,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    }
    last_error: Optional[Exception] = None
    async with httpx.AsyncClient(timeout=15) as client:
        for attempt in range(3):
            try:
                response = await client.post(
                    f"{settings.waffo_content_safety_api_base.rstrip('/')}{WAFFO_CONTENT_SAFETY_PATH}",
                    headers=headers,
                    content=body_json,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                break
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "content_safety_rate_limited",
                        "message": "Content safety check is rate limited. Please retry later.",
                        "retry_after": retry_after,
                    },
                )
            if 500 <= response.status_code < 600:
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "content_safety_unavailable",
                        "message": "Content safety check is temporarily unavailable. Please retry later.",
                    },
                )
            if response.status_code >= 400:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "content_safety_failed",
                        "message": "Content safety check rejected the request.",
                    },
                )
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict):
                return data
            if isinstance(payload, dict):
                return payload
            break
    if last_error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "content_safety_unavailable",
                "message": "Content safety check is temporarily unavailable. Please retry later.",
            },
        )
    return {"action": "review", "reasonCode": "service_degraded"}


def _raise_for_content_safety(verdict: dict[str, Any]) -> None:
    action = verdict.get("action")
    if action == "allow":
        return
    reason = verdict.get("reasonCode")
    message = (
        "This request does not comply with Catea's AI usage policy."
        if action == "block"
        else "This request requires safety review. Please retry later."
    )
    raise HTTPException(
        status_code=403,
        detail={
            "code": "content_safety_rejected",
            "message": message,
            "action": action or "review",
            "reasonCode": reason,
            "requestId": verdict.get("requestId"),
            "matchedCategories": verdict.get("matchedCategories") or [],
        },
    )


def _usage_tokens(payload: dict[str, Any], fallback: int) -> tuple[int, int, int]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return fallback, 0, fallback

    def token_count(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    prompt = token_count(usage.get("prompt_tokens"))
    completion = token_count(usage.get("completion_tokens"))
    total = token_count(usage.get("total_tokens")) or prompt + completion or fallback
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
    # Serialize successful usage updates so concurrent requests cannot overwrite
    # each other's counters while retaining separate append-only usage events.
    locked_period = await db.scalar(
        select(BillingUsagePeriod)
        .where(BillingUsagePeriod.id == period.id)
        .with_for_update()
    )
    locked_window = await db.scalar(
        select(BillingUsageWindow)
        .where(BillingUsageWindow.id == window.id)
        .with_for_update()
    )
    balance = await db.scalar(
        select(BillingCreditBalance)
        .where(BillingCreditBalance.customer_id == customer.id)
        .with_for_update()
    )
    if not locked_period or not locked_window:
        raise RuntimeError("Hosted usage bucket disappeared before accounting")
    if not balance:
        balance = await _get_credit_balance(db, customer)
    event = BillingUsageEvent(
        customer_id=customer.id,
        request_id=request_id,
        model_route=model_route,
        input_tokens=max(0, input_tokens),
        output_tokens=max(0, output_tokens),
        credits=safe_credits,
    )
    monthly_remaining = max(0, locked_period.included_credits - locked_period.used_credits)
    monthly_credits = min(monthly_remaining, safe_credits)
    topup_credits = max(0, safe_credits - monthly_credits)
    locked_period.used_credits = min(
        locked_period.included_credits,
        locked_period.used_credits + monthly_credits,
    )
    if topup_credits:
        balance.balance_credits = max(0, balance.balance_credits - topup_credits)
        db.add(balance)
    locked_window.used_credits = min(
        locked_window.included_credits,
        locked_window.used_credits + safe_credits,
    )
    db.add(event)
    db.add(locked_period)
    db.add(locked_window)
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


def _xorpay_sign(*parts: str) -> str:
    return hashlib.md5("".join(parts).encode("utf-8")).hexdigest().lower()


def _xorpay_price(value: str) -> str:
    try:
        return f"{float(value):.2f}"
    except ValueError:
        raise HTTPException(status_code=500, detail="XorPay CNY price is not configured correctly")


def _xorpay_form(raw_body: bytes) -> dict[str, str]:
    parsed = parse_qs(raw_body.decode("utf-8", "ignore"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _verify_xorpay_callback(data: dict[str, str]) -> bool:
    if not settings.xorpay_app_secret:
        return False
    expected = _xorpay_sign(
        data.get("aoid", ""),
        data.get("order_id", ""),
        data.get("pay_price", ""),
        data.get("pay_time", ""),
        settings.xorpay_app_secret,
    )
    return hmac.compare_digest(expected, data.get("sign", ""))


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


async def _grant_waffo_credits_from_event(
    db: AsyncSession,
    event: dict[str, Any],
    customer: BillingCustomer,
    product_id: str,
    credits: int,
) -> None:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    metadata = data.get("orderMetadata") if isinstance(data.get("orderMetadata"), dict) else {}
    provider_event_id = str(event.get("id") or event.get("eventId") or data.get("orderId") or data.get("id"))
    if not provider_event_id or provider_event_id == "None":
        provider_event_id = f"waffo:{customer.uuid}:{product_id}:{int(_now().timestamp())}"

    order = data.get("order") if isinstance(data.get("order"), dict) else {}
    provider_order_id = _first_string(
        data.get("orderId"),
        data.get("order_id"),
        order.get("id"),
        event.get("orderId"),
        data.get("id"),
        provider_event_id,
    )
    if not provider_order_id:
        provider_order_id = provider_event_id
    canonical_grant_id = f"order:{provider_order_id}"

    # Serialize grants for one customer before checking order idempotency. Waffo
    # can emit payment.completed and order.completed for the same purchase, and
    # reconciliation may observe it again later under a third event id.
    await db.scalar(
        select(BillingCustomer.id)
        .where(BillingCustomer.id == customer.id)
        .with_for_update()
    )

    existing = await db.execute(
        select(BillingCreditGrant).where(
            BillingCreditGrant.provider == "waffo",
            BillingCreditGrant.customer_id == customer.id,
        )
    )
    for prior_grant in existing.scalars():
        if prior_grant.provider_event_id == canonical_grant_id:
            return
        prior_metadata = prior_grant.provider_metadata if isinstance(prior_grant.provider_metadata, dict) else {}
        prior_event = prior_metadata.get("event") if isinstance(prior_metadata.get("event"), dict) else {}
        prior_data = prior_event.get("data") if isinstance(prior_event.get("data"), dict) else {}
        prior_order = prior_data.get("order") if isinstance(prior_data.get("order"), dict) else {}
        prior_order_id = _first_string(
            prior_data.get("orderId"),
            prior_data.get("order_id"),
            prior_order.get("id"),
            prior_event.get("orderId"),
            prior_data.get("id"),
        )
        if prior_order_id == provider_order_id:
            return

    balance = await db.scalar(
        select(BillingCreditBalance)
        .where(BillingCreditBalance.customer_id == customer.id)
        .with_for_update()
    )
    if not balance:
        balance = await _get_credit_balance(db, customer)
    safe_credits = max(1, credits)
    balance.balance_credits += safe_credits
    grant = BillingCreditGrant(
        customer_id=customer.id,
        provider="waffo",
        provider_event_id=canonical_grant_id,
        product_id=product_id,
        credits=safe_credits,
        currency=_first_string(data.get("currency"), metadata.get("currency")),
        amount=None,
        provider_metadata={
            "event": event,
            "metadata": metadata,
            "source_event_id": provider_event_id,
            "provider_order_id": provider_order_id,
        },
    )
    db.add(balance)
    db.add(grant)
    await db.flush()


async def _reconcile_waffo_credit_packs_for_customer(db: AsyncSession, customer: BillingCustomer) -> None:
    if settings.billing_provider.lower() != "waffo":
        return
    if not settings.waffo_merchant_id or not settings.waffo_private_key or not settings.waffo_store_id:
        return

    product_ids = {plan: product_id for plan, product_id in _credit_product_ids().items() if product_id}
    if not product_ids:
        return

    query = """
    query($storeId:String!, $email:String!, $productIds:[String!]) {
      onetimeOrders(
        storeId:$storeId,
        limit:50,
        filter:{buyerEmail:{eq:$email}, productId:{in:$productIds}, status:{eq:"completed"}},
        orderBy:[created_at_desc]
      ) {
        id
        buyerEmail
        status
        currency
        metadata
        productVersion { productId }
        onetimeProduct { id }
        total { amount currency }
        payments { id status }
      }
    }
    """
    body = {
        "query": query,
        "variables": {
            "storeId": settings.waffo_store_id,
            "email": customer.email,
            "productIds": list(product_ids.values()),
        },
    }
    body_json = _waffo_body_json(body)
    timestamp = str(int(_now().timestamp()))
    signature = _waffo_signature("POST", WAFFO_GRAPHQL_PATH, timestamp, body_json)

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{settings.waffo_api_base.rstrip('/')}{WAFFO_GRAPHQL_PATH}",
            headers={
                "Content-Type": "application/json",
                "X-Merchant-Id": settings.waffo_merchant_id,
                "X-Timestamp": timestamp,
                "X-Signature": signature,
            },
            content=body_json,
        )
    if response.status_code >= 400:
        return
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return
    if payload.get("errors"):
        return

    orders = ((payload.get("data") or {}).get("onetimeOrders") or [])
    product_to_pack = {product_id: (plan, CREDIT_PACKS[plan]) for plan, product_id in product_ids.items()}
    for order in orders:
        if not isinstance(order, dict) or str(order.get("status") or "").lower() != "completed":
            continue
        product_id = _first_string(
            ((order.get("onetimeProduct") or {}).get("id") if isinstance(order.get("onetimeProduct"), dict) else None),
            ((order.get("productVersion") or {}).get("productId") if isinstance(order.get("productVersion"), dict) else None),
        )
        pack = product_to_pack.get(product_id or "")
        if not pack:
            continue
        payments = order.get("payments") if isinstance(order.get("payments"), list) else []
        if payments and not any(str(payment.get("status") or "").lower() in {"succeeded", "completed"} for payment in payments if isinstance(payment, dict)):
            continue
        metadata = {}
        if isinstance(order.get("metadata"), str) and order.get("metadata"):
            try:
                parsed_metadata = json.loads(order["metadata"])
                if isinstance(parsed_metadata, dict):
                    metadata = parsed_metadata
            except json.JSONDecodeError:
                metadata = {}
        event = {
            "id": f"reconcile:{order.get('id')}",
            "eventType": "order.completed",
            "data": {
                "orderId": order.get("id"),
                "buyerEmail": customer.email,
                "currency": order.get("currency"),
                "productId": product_id,
                "orderMetadata": metadata,
                "total": order.get("total"),
                "payments": payments,
            },
        }
        await _grant_waffo_credits_from_event(db, event, customer, product_id or "credits_pack", int(pack[1]["credits"]))


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
    credit_pack_match = _credit_pack_for_product(product_id)
    if credit_pack_match or metadata.get("billingMode") == "credits_pack":
        pack_credits = int(metadata.get("credits") or (credit_pack_match[1]["credits"] if credit_pack_match else 0) or 0)
        if pack_credits <= 0:
            return
        customer = await _get_or_create_customer(db, email, provider_customer_id=None, provider="waffo")
        await _grant_waffo_credits_from_event(db, event, customer, product_id or "credits_pack", pack_credits)
        return

    one_time_pass = bool(
        product_id
        and (
            product_id == settings.waffo_product_id_pro_30d
            or metadata.get("billingMode") == "one_time_30d"
        )
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

    if one_time_pass and active:
        now = _now()
        base = record.current_period_end if record.current_period_end and record.current_period_end > now else now
        current_period_start = now
        current_period_end = base + timedelta(days=30)

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


async def _create_xorpay_checkout(
    payload: BillingCheckoutRequest,
    db: AsyncSession,
) -> BillingCheckoutResponse:
    """Create a XorPay cashier payment URL for Catea Pro CNY payments."""
    if not settings.xorpay_aid or not settings.xorpay_app_secret:
        raise HTTPException(status_code=500, detail="XorPay credentials are not configured")
    notify_url = settings.xorpay_notify_url or f"{settings.catea_billing_success_url.rstrip('/').removesuffix('/success')}/xorpay/webhook"
    if not notify_url.startswith("https://"):
        raise HTTPException(status_code=500, detail="XorPay notify_url must be a public HTTPS URL")
    if payload.currency != "CNY":
        raise HTTPException(status_code=400, detail="XorPay is configured for CNY payments only")

    await _ensure_pro_plan(db)
    customer = await _get_or_create_customer(db, payload.email, provider="xorpay")
    await _ensure_billing_user(db, customer.email)
    product_id = _xorpay_product_id()
    order_id = f"catea_{customer.uuid.replace('-', '')}_{int(_now().timestamp())}"
    price = _xorpay_price(settings.xorpay_pro_monthly_price_cny)
    pay_type = settings.xorpay_pay_type or "cashier"
    if pay_type not in {"cashier", "native", "alipay"}:
        raise HTTPException(status_code=500, detail="XorPay pay_type must be cashier, native, or alipay")
    name = "Catea Pro Monthly"
    more = json.dumps(
        {
            "email": customer.email,
            "licenseKey": customer.license_key,
            "plan": PRO_PLAN_ID,
            "productId": product_id,
            "source": "catea",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    sign = _xorpay_sign(name, pay_type, price, order_id, notify_url, settings.xorpay_app_secret)
    params = {
        "name": name,
        "pay_type": pay_type,
        "price": price,
        "order_id": order_id,
        "notify_url": notify_url,
        "order_uid": customer.uuid,
        "more": more,
        "expire": "7200",
        "sign": sign,
    }
    checkout_url = f"{settings.xorpay_api_base.rstrip('/')}/api/cashier/{settings.xorpay_aid}?{urlencode(params)}"
    return BillingCheckoutResponse(
        checkout_id=order_id,
        checkout_url=checkout_url,
        product_id=product_id,
        plan=PRO_PLAN_ID,
    )


async def _upsert_xorpay_subscription_from_callback(
    db: AsyncSession,
    data: dict[str, str],
) -> None:
    order_id = data.get("order_id", "").strip()
    if not order_id:
        raise HTTPException(status_code=400, detail="Missing XorPay order_id")

    metadata: dict[str, Any] = {}
    more = data.get("more")
    if more:
        try:
            parsed_more = json.loads(more)
            if isinstance(parsed_more, dict):
                metadata = parsed_more
        except json.JSONDecodeError:
            metadata = {"more": more}
    email = _first_string(metadata.get("email"))
    if not email:
        raise HTTPException(status_code=400, detail="Missing XorPay customer email")

    product_id = _first_string(metadata.get("productId"), _xorpay_product_id())
    pay_time = _parse_datetime(data.get("pay_time")) or _now()
    period_start = pay_time
    period_end = period_start + timedelta(days=30)
    customer = await _get_or_create_customer(db, email, provider_customer_id=None, provider="xorpay")
    result = await db.execute(
        select(BillingSubscription).where(
            BillingSubscription.provider == "xorpay",
            BillingSubscription.provider_subscription_id == order_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        record = BillingSubscription(
            customer_id=customer.id,
            provider="xorpay",
            provider_subscription_id=order_id,
            plan=_plan_for_product(product_id),
            status="active",
        )
        db.add(record)

    record.customer_id = customer.id
    record.provider_order_id = order_id
    record.provider_checkout_id = data.get("aoid") or order_id
    record.product_id = product_id
    record.plan = _plan_for_product(product_id)
    record.status = "active"
    record.active = True
    record.current_period_start = period_start
    record.current_period_end = period_end
    record.canceled_at = None
    record.provider_metadata = {
        "callback": data,
        "metadata": metadata,
    }
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
    credit_pack = _credit_pack_for_plan(payload.plan)
    one_time_pass = bool(credit_pack) or payload.plan == "pro_30d" or payload.currency == "CNY"
    checkout_currency = payload.currency if credit_pack else ("CNY" if one_time_pass else "USD")
    product_type = "onetime" if one_time_pass else "subscription"

    customer = await _get_or_create_customer(db, payload.email, provider="waffo")
    await _ensure_billing_user(db, customer.email)
    success_url = payload.success_url or settings.catea_billing_success_url
    request_id = f"catea_{customer.uuid}_{int(_now().timestamp())}"
    body = {
        "storeId": settings.waffo_store_id,
        "productId": product_id,
        "productType": product_type,
        "currency": checkout_currency,
        "buyerEmail": customer.email,
        "successUrl": success_url,
        "metadata": {
            "referenceId": customer.uuid,
            "email": customer.email,
            "licenseKey": customer.license_key,
            "plan": payload.plan if credit_pack else PRO_PLAN_ID,
            "currency": checkout_currency,
            "requestedCurrency": payload.currency,
            "billingMode": "credits_pack" if credit_pack else ("one_time_30d" if one_time_pass else "subscription_monthly"),
            "credits": credit_pack["credits"] if credit_pack else None,
            "productId": product_id,
            "source": "catea",
        },
        "orderMerchantExternalId": request_id,
    }
    if one_time_pass and checkout_currency == "CNY":
        body["includePaymentMethods"] = ["wechat"]
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
        plan=payload.plan if credit_pack else PRO_PLAN_ID,
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
    if settings.billing_provider.lower() == "xorpay" or payload.currency == "CNY":
        return await _create_xorpay_checkout(payload, db)
    return await _create_creem_checkout(payload, db)


@router.post("/xorpay/checkout", response_model=BillingCheckoutResponse)
async def create_xorpay_checkout(
    payload: BillingCheckoutRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a XorPay cashier payment URL for Catea Pro CNY payments."""
    return await _create_xorpay_checkout(payload, db)


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


@router.post("/xorpay/webhook")
async def xorpay_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Verify and process XorPay payment notifications."""
    raw_body = await request.body()
    data = _xorpay_form(raw_body)
    if not _verify_xorpay_callback(data):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid XorPay signature")

    event_id = data.get("aoid") or data.get("order_id")
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing XorPay event id")
    result = await db.execute(
        select(BillingWebhookEvent).where(
            BillingWebhookEvent.provider == "xorpay",
            BillingWebhookEvent.provider_event_id == event_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing and existing.processed:
        return PlainTextResponse("success")

    event_record = existing or BillingWebhookEvent(
        provider="xorpay",
        provider_event_id=event_id,
        event_type="payment.success",
        payload=data,
    )
    if not existing:
        db.add(event_record)

    try:
        await _upsert_xorpay_subscription_from_callback(db, data)
        event_record.processed = True
        event_record.processed_at = _now()
        event_record.error_message = None
        await db.commit()
    except Exception as exc:
        event_record.processed = False
        event_record.error_message = str(exc)
        await db.flush()
        raise

    return PlainTextResponse("success")


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
        await _reconcile_waffo_credit_packs_for_customer(db, customer)
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


@router.get("/hosted/status")
async def hosted_status(
    authorization: Optional[str] = Header(default=None),
    x_catea_license: Optional[str] = Header(default=None, alias="X-Catea-License"),
    db: AsyncSession = Depends(get_db),
):
    """Validate hosted entitlement and report quota without calling the model."""
    request_id = "hosted_status_" + secrets.token_urlsafe(18)
    license_key = (x_catea_license or "").strip() or _bearer_token(authorization)
    if not license_key:
        return _hosted_error_response(
            request_id,
            status.HTTP_401_UNAUTHORIZED,
            "authorization_required",
            "Catea Pro authorization is required.",
        )
    result = await db.execute(select(BillingCustomer).where(BillingCustomer.license_key == license_key))
    customer = result.scalar_one_or_none()
    if not customer:
        return _hosted_error_response(
            request_id,
            status.HTTP_401_UNAUTHORIZED,
            "authorization_failed",
            "Catea Pro authorization failed. Refresh your plan status and try again.",
        )
    subscription = await _get_subscription_status(db, customer)
    if not subscription or not subscription.active:
        return _hosted_error_response(
            request_id,
            status.HTTP_402_PAYMENT_REQUIRED,
            "subscription_required",
            "An active Catea Pro subscription is required.",
        )
    plan = await _ensure_pro_plan(db)
    quota = await _quota_for_subscription(db, customer, subscription, plan)
    return JSONResponse(
        headers={HOSTED_REQUEST_ID_HEADER: request_id},
        content={
            "ok": True,
            "provider_configured": bool(settings.catea_hosted_model_api_key),
            "quota": jsonable_encoder(quota),
        },
    )


@router.post("/hosted/v1/chat/completions")
async def hosted_chat_completions(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_catea_license: Optional[str] = Header(default=None, alias="X-Catea-License"),
    db: AsyncSession = Depends(get_db),
):
    """OpenAI-compatible hosted model endpoint for Catea Pro users."""
    request_id = "hosted_" + secrets.token_urlsafe(24)
    if not settings.catea_hosted_model_api_key:
        logger.error("Hosted request %s rejected: model provider is not configured", request_id)
        return _hosted_error_response(
            request_id,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "service_not_configured",
            "Catea's hosted model is temporarily unavailable. Please try again later.",
        )
    license_key = (x_catea_license or "").strip() or _bearer_token(authorization)
    if not license_key:
        return _hosted_error_response(
            request_id,
            status.HTTP_401_UNAUTHORIZED,
            "authorization_required",
            "Catea Pro authorization is required.",
        )

    try:
        customer, _, _, period, window = await _get_active_hosted_customer(db, license_key)
    except HTTPException as exc:
        logger.info("Hosted request %s rejected by entitlement status=%s", request_id, exc.status_code)
        return _hosted_http_error_response(request_id, exc)
    except Exception:
        await db.rollback()
        logger.exception("Hosted request %s entitlement lookup failed", request_id)
        return _hosted_error_response(
            request_id,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "entitlement_unavailable",
            "Catea Pro authorization is temporarily unavailable. Please try again later.",
        )
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return _hosted_error_response(
            request_id,
            400,
            "invalid_json",
            "The request body must be valid JSON.",
        )
    if not isinstance(payload, dict):
        return _hosted_error_response(
            request_id,
            400,
            "invalid_request",
            "The request body must be a JSON object.",
        )
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return _hosted_error_response(request_id, 400, "invalid_messages", "messages must be an array.")
    prompt_tokens = _estimate_tokens_from_messages(messages)
    try:
        safety_verdict = await _scan_waffo_prompt(
            _latest_user_prompt(messages),
            _content_safety_locale(payload),
        )
        _raise_for_content_safety(safety_verdict)
    except HTTPException as exc:
        logger.info("Hosted request %s rejected by content safety status=%s", request_id, exc.status_code)
        return _hosted_http_error_response(request_id, exc)
    except Exception:
        logger.exception("Hosted request %s content safety check failed", request_id)
        return _hosted_error_response(
            request_id,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "content_safety_unavailable",
            "Catea's safety check is temporarily unavailable. Please try again later.",
        )
    outbound_body = _hosted_model_body(payload)
    headers = {
        "Authorization": f"Bearer {settings.catea_hosted_model_api_key}",
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(settings.catea_hosted_model_timeout_s, connect=20.0)
    logger.info(
        "Hosted request %s started customer=%s stream=%s model=%s",
        request_id,
        customer.uuid,
        payload.get("stream") is True,
        settings.catea_hosted_model_name,
    )
    if payload.get("stream") is True:
        client = httpx.AsyncClient(timeout=timeout)
        try:
            upstream_request = client.build_request(
                "POST",
                _hosted_model_url(),
                headers=headers,
                json=outbound_body,
            )
            response = await client.send(upstream_request, stream=True)
        except httpx.TimeoutException:
            await client.aclose()
            logger.warning("Hosted request %s timed out before stream start", request_id)
            return _hosted_error_response(
                request_id,
                status.HTTP_504_GATEWAY_TIMEOUT,
                "upstream_timeout",
                "Catea's hosted model timed out. Please try again.",
            )
        except httpx.HTTPError as exc:
            await client.aclose()
            logger.warning(
                "Hosted request %s could not reach upstream: %s",
                request_id,
                type(exc).__name__,
            )
            return _hosted_error_response(
                request_id,
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "upstream_unavailable",
                "Catea's hosted model is temporarily unavailable. Please try again later.",
            )
        if response.status_code >= 400:
            upstream_status = response.status_code
            retry_after = response.headers.get("Retry-After")
            await response.aread()
            await response.aclose()
            await client.aclose()
            logger.warning(
                "Hosted request %s upstream rejected stream status=%s",
                request_id,
                upstream_status,
            )
            return _hosted_upstream_error_response(request_id, upstream_status, retry_after)
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" not in content_type:
            await response.aread()
            await response.aclose()
            await client.aclose()
            logger.warning(
                "Hosted request %s received non-stream response content_type=%s",
                request_id,
                content_type or "unknown",
            )
            return _hosted_error_response(
                request_id,
                status.HTTP_502_BAD_GATEWAY,
                "upstream_protocol_error",
                "Catea's hosted model returned an invalid response. Please try again later.",
            )

        async def generate_stream():
            output_chars = 0
            completed = False
            upstream_usage: Optional[tuple[int, int, int]] = None
            try:
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
                                if isinstance(chunk, dict) and isinstance(chunk.get("usage"), dict):
                                    upstream_usage = _usage_tokens(chunk, prompt_tokens)
                                delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                                content = delta.get("content")
                                if isinstance(content, str):
                                    output_chars += len(content)
                            except (json.JSONDecodeError, AttributeError, IndexError, TypeError):
                                pass
                    yield f"{line}\n\n".encode("utf-8")
                    await asyncio.sleep(0)
            except httpx.HTTPError as exc:
                logger.warning(
                    "Hosted request %s stream interrupted: %s",
                    request_id,
                    type(exc).__name__,
                )
                error = {
                    "error": {
                        "message": "Catea's hosted model connection was interrupted. Please try again.",
                        "type": "catea_hosted_error",
                        "code": "upstream_stream_interrupted",
                        "request_id": request_id,
                    }
                }
                yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n".encode("utf-8")
            finally:
                await response.aclose()
                await client.aclose()
            if not completed:
                return
            fallback_output_tokens = max(0, output_chars // 4)
            input_tokens, output_tokens, total_tokens = upstream_usage or (
                prompt_tokens,
                fallback_output_tokens,
                prompt_tokens + fallback_output_tokens,
            )
            try:
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
            except Exception:
                await db.rollback()
                logger.exception("Hosted request %s usage accounting failed after stream", request_id)
            else:
                logger.info("Hosted request %s completed stream credits=%s", request_id, total_tokens)

        return StreamingResponse(
            generate_stream(),
            media_type="text/event-stream",
            headers={
                HOSTED_REQUEST_ID_HEADER: request_id,
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(_hosted_model_url(), headers=headers, json=outbound_body)
    except httpx.TimeoutException:
        logger.warning("Hosted request %s timed out", request_id)
        return _hosted_error_response(
            request_id,
            status.HTTP_504_GATEWAY_TIMEOUT,
            "upstream_timeout",
            "Catea's hosted model timed out. Please try again.",
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "Hosted request %s could not reach upstream: %s",
            request_id,
            type(exc).__name__,
        )
        return _hosted_error_response(
            request_id,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "upstream_unavailable",
            "Catea's hosted model is temporarily unavailable. Please try again later.",
        )
    if response.status_code >= 400:
        logger.warning(
            "Hosted request %s upstream rejected status=%s",
            request_id,
            response.status_code,
        )
        return _hosted_upstream_error_response(
            request_id,
            response.status_code,
            response.headers.get("Retry-After"),
        )
    try:
        body = response.json()
    except json.JSONDecodeError:
        logger.warning(
            "Hosted request %s received non-JSON response content_type=%s",
            request_id,
            response.headers.get("content-type") or "unknown",
        )
        return _hosted_error_response(
            request_id,
            status.HTTP_502_BAD_GATEWAY,
            "upstream_protocol_error",
            "Catea's hosted model returned an invalid response. Please try again later.",
        )
    if not isinstance(body, dict):
        logger.warning("Hosted request %s received non-object JSON response", request_id)
        return _hosted_error_response(
            request_id,
            status.HTTP_502_BAD_GATEWAY,
            "upstream_protocol_error",
            "Catea's hosted model returned an invalid response. Please try again later.",
        )
    input_tokens, output_tokens, total_tokens = _usage_tokens(body, prompt_tokens)
    try:
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
    except Exception:
        await db.rollback()
        logger.exception("Hosted request %s usage accounting failed after response", request_id)
    else:
        logger.info("Hosted request %s completed credits=%s", request_id, total_tokens)
    return JSONResponse(content=body, headers={HOSTED_REQUEST_ID_HEADER: request_id})


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
