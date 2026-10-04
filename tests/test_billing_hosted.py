"""Focused regression tests for Catea hosted-model error and usage contracts."""

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BillingCreditBalance,
    BillingCreditGrant,
    BillingCustomer,
    BillingPlan,
    BillingSubscription,
    BillingUsageEvent,
    BillingUsagePeriod,
    BillingUsageWindow,
)
from app.routers.billing import (
    HOSTED_REQUEST_ID_HEADER,
    _grant_waffo_credits_from_event,
    _hosted_upstream_error_response,
    _record_hosted_usage,
    hosted_status,
)


def credit_event(event_id: str, order_id: str, event_type: str = "order.completed") -> dict:
    return {
        "id": event_id,
        "eventType": event_type,
        "data": {
            "orderId": order_id,
            "currency": "USD",
            "orderMetadata": {"billingMode": "credits_pack"},
        },
    }


def test_hosted_upstream_errors_hide_provider_details():
    response = _hosted_upstream_error_response("hosted_test", 401)

    assert response.status_code == 503
    assert response.headers[HOSTED_REQUEST_ID_HEADER] == "hosted_test"
    body = json.loads(response.body)
    assert body == {
        "error": {
            "message": "Catea's hosted model is temporarily unavailable. Please try again later.",
            "type": "catea_hosted_error",
            "code": "upstream_unavailable",
            "request_id": "hosted_test",
        }
    }
    assert "401" not in response.body.decode()


def test_hosted_rate_limit_preserves_retry_hint():
    response = _hosted_upstream_error_response("hosted_rate", 429, "12")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "12"
    assert json.loads(response.body)["error"]["code"] == "upstream_rate_limited"


@pytest.mark.asyncio
async def test_hosted_status_does_not_call_model_or_consume_usage(db_session: AsyncSession):
    now = datetime.utcnow()
    customer = BillingCustomer(
        email="hosted-status@example.com",
        provider="waffo",
        license_key="catea_hosted_status_test",
    )
    db_session.add(customer)
    await db_session.flush()
    db_session.add(
        BillingSubscription(
            customer_id=customer.id,
            provider="waffo",
            provider_subscription_id="subscription_hosted_status",
            plan="pro_monthly",
            status="active",
            active=True,
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
    )
    await db_session.commit()

    response = await hosted_status(
        authorization=None,
        x_catea_license=customer.license_key,
        db=db_session,
    )
    body = json.loads(response.body)
    usage_events = (
        await db_session.scalars(
            select(BillingUsageEvent).where(BillingUsageEvent.customer_id == customer.id)
        )
    ).all()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["quota"]["window"]["used_credits"] == 0
    assert usage_events == []


@pytest.mark.asyncio
async def test_hosted_usage_consumes_monthly_before_topup(db_session: AsyncSession):
    now = datetime.utcnow()
    customer = BillingCustomer(
        email="hosted-usage@example.com",
        provider="waffo",
        license_key="catea_hosted_usage_test",
    )
    plan = BillingPlan(
        plan_id="hosted_usage_test",
        name="Hosted usage test",
        monthly_credits=100,
        window_credits=1_000,
        window_hours=5,
    )
    db_session.add_all([customer, plan])
    await db_session.flush()
    period = BillingUsagePeriod(
        customer_id=customer.id,
        plan_id=plan.id,
        period_start=now,
        period_end=now + timedelta(days=30),
        included_credits=100,
        used_credits=90,
    )
    window = BillingUsageWindow(
        customer_id=customer.id,
        plan_id=plan.id,
        window_start=now,
        window_end=now + timedelta(hours=5),
        included_credits=1_000,
        used_credits=0,
    )
    balance = BillingCreditBalance(customer_id=customer.id, balance_credits=50)
    db_session.add_all([period, window, balance])
    await db_session.commit()

    await _record_hosted_usage(
        db_session,
        customer,
        period,
        window,
        request_id="hosted_usage_order",
        model_route="test/model",
        input_tokens=15,
        output_tokens=15,
        credits=30,
    )

    await db_session.refresh(period)
    await db_session.refresh(window)
    await db_session.refresh(balance)
    event = await db_session.scalar(
        select(BillingUsageEvent).where(BillingUsageEvent.request_id == "hosted_usage_order")
    )
    assert period.used_credits == 100
    assert balance.balance_credits == 30
    assert window.used_credits == 30
    assert event is not None and event.credits == 30


@pytest.mark.asyncio
async def test_credit_grant_is_idempotent_by_order_across_event_types(db_session: AsyncSession):
    customer = BillingCustomer(
        email="credit-order@example.com",
        provider="waffo",
        license_key="catea_credit_order_test",
    )
    db_session.add(customer)
    await db_session.flush()

    await _grant_waffo_credits_from_event(
        db_session,
        credit_event("evt_payment", "order_same", "payment.completed"),
        customer,
        "credits_20k",
        20_000,
    )
    await _grant_waffo_credits_from_event(
        db_session,
        credit_event("evt_order", "order_same", "order.completed"),
        customer,
        "credits_20k",
        20_000,
    )
    await _grant_waffo_credits_from_event(
        db_session,
        credit_event("reconcile:order_same", "order_same"),
        customer,
        "credits_20k",
        20_000,
    )
    await db_session.commit()

    balance = await db_session.scalar(
        select(BillingCreditBalance).where(BillingCreditBalance.customer_id == customer.id)
    )
    grants = (
        await db_session.scalars(
            select(BillingCreditGrant).where(BillingCreditGrant.customer_id == customer.id)
        )
    ).all()
    assert balance is not None and balance.balance_credits == 20_000
    assert len(grants) == 1
    assert grants[0].provider_event_id == "order:order_same"


@pytest.mark.asyncio
async def test_credit_grants_from_distinct_orders_stack(db_session: AsyncSession):
    customer = BillingCustomer(
        email="credit-stack@example.com",
        provider="waffo",
        license_key="catea_credit_stack_test",
    )
    db_session.add(customer)
    await db_session.flush()

    await _grant_waffo_credits_from_event(
        db_session,
        credit_event("evt_small", "order_small"),
        customer,
        "credits_20k",
        20_000,
    )
    await _grant_waffo_credits_from_event(
        db_session,
        credit_event("evt_value", "order_value"),
        customer,
        "credits_50k",
        50_000,
    )
    await db_session.commit()

    balance = await db_session.scalar(
        select(BillingCreditBalance).where(BillingCreditBalance.customer_id == customer.id)
    )
    grants = (
        await db_session.scalars(
            select(BillingCreditGrant).where(BillingCreditGrant.customer_id == customer.id)
        )
    ).all()
    assert balance is not None and balance.balance_credits == 70_000
    assert len(grants) == 2


@pytest.mark.asyncio
async def test_credit_grant_recognizes_legacy_reconciliation_record(db_session: AsyncSession):
    customer = BillingCustomer(
        email="credit-legacy@example.com",
        provider="waffo",
        license_key="catea_credit_legacy_test",
    )
    db_session.add(customer)
    await db_session.flush()
    db_session.add_all(
        [
            BillingCreditBalance(customer_id=customer.id, balance_credits=20_000),
            BillingCreditGrant(
                customer_id=customer.id,
                provider="waffo",
                provider_event_id="reconcile:order_legacy",
                product_id="credits_20k",
                credits=20_000,
                provider_metadata={
                    "event": credit_event("reconcile:order_legacy", "order_legacy"),
                    "metadata": {"billingMode": "credits_pack"},
                },
            ),
        ]
    )
    await db_session.commit()

    await _grant_waffo_credits_from_event(
        db_session,
        credit_event("evt_order_legacy", "order_legacy"),
        customer,
        "credits_20k",
        20_000,
    )
    await db_session.commit()

    balance = await db_session.scalar(
        select(BillingCreditBalance).where(BillingCreditBalance.customer_id == customer.id)
    )
    grants = (
        await db_session.scalars(
            select(BillingCreditGrant).where(BillingCreditGrant.customer_id == customer.id)
        )
    ).all()
    assert balance is not None and balance.balance_credits == 20_000
    assert len(grants) == 1
