"""Focused regression tests for Catea hosted-model error and usage contracts."""

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BillingCreditBalance,
    BillingCustomer,
    BillingPlan,
    BillingUsageEvent,
    BillingUsagePeriod,
    BillingUsageWindow,
)
from app.routers.billing import (
    HOSTED_REQUEST_ID_HEADER,
    _hosted_upstream_error_response,
    _record_hosted_usage,
)


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
