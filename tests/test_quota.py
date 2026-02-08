"""
Quota Management API tests for Asgard API.
"""
import pytest
from datetime import datetime, timedelta
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, APIKey, Agent, UsageLog
from app.auth import get_password_hash, hash_api_key, generate_api_key


class TestQuotaManagement:
    """Test quota management functionality."""

    @pytest.mark.asyncio
    async def test_quota_initial_state(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str]
    ):
        """Test that new API key has zero used quota."""
        api_key_obj, _ = test_api_key
        assert api_key_obj.used_quota == 0.0

    @pytest.mark.asyncio
    async def test_quota_accumulates(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        db_session: AsyncSession,
        test_agents: list
    ):
        """Test that quota accumulates with multiple requests."""
        api_key_obj, api_key_value = test_api_key

        # Make multiple requests
        for i in range(3):
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "asgard/code-refactor",
                    "messages": [{"role": "user", "content": f"Test message {i}"}]
                },
                headers={"X-API-Key": api_key_value}
            )
            assert response.status_code == 200

        # Refresh and check accumulated quota
        await db_session.refresh(api_key_obj)
        assert api_key_obj.used_quota > 0

    @pytest.mark.asyncio
    async def test_quota_limit_enforcement(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User
    ):
        """Test that quota limit is enforced."""
        # Create API key with very low quota
        api_key_value, prefix = generate_api_key()
        low_quota_key = APIKey(
            key_hash=hash_api_key(api_key_value),
            key_prefix=prefix,
            name="Very Low Quota Key",
            user_id=test_user.id,
            quota_limit=0.001,  # Very small quota
            used_quota=0.0,
            is_active=True,
        )
        db_session.add(low_quota_key)
        await db_session.commit()

        # First request should succeed
        # Create test agent first
        agent = Agent(
            agent_id="asgard/test-agent",
            name="Test Agent",
            description="For testing",
            category="test",
            pricing=0.01,
            is_active=True,
            is_public=True,
        )
        db_session.add(agent)
        await db_session.commit()

        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/test-agent",
                "messages": [{"role": "user", "content": "Test"}]
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 429  # Quota exceeded

    @pytest.mark.asyncio
    async def test_no_quota_limit_unlimited(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User
    ):
        """Test that keys without quota limit can make unlimited requests."""
        # Create API key without quota limit
        api_key_value, prefix = generate_api_key()
        unlimited_key = APIKey(
            key_hash=hash_api_key(api_key_value),
            key_prefix=prefix,
            name="Unlimited Key",
            user_id=test_user.id,
            quota_limit=None,  # No limit
            used_quota=100.0,
            is_active=True,
        )
        db_session.add(unlimited_key)
        await db_session.commit()

        # Create test agent
        agent = Agent(
            agent_id="asgard/test-unlimited",
            name="Test Unlimited Agent",
            description="For testing",
            category="test",
            pricing=0.01,
            is_active=True,
            is_public=True,
        )
        db_session.add(agent)
        await db_session.commit()

        # Should not get quota exceeded error
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/test-unlimited",
                "messages": [{"role": "user", "content": "Test"}]
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 200


class TestUsageStatistics:
    """Test usage statistics functionality."""

    @pytest.mark.asyncio
    async def test_get_usage_stats(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test getting usage statistics."""
        # Make at least one request first
        api_key_obj, api_key_value = test_api_key
        await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Test"}]
            },
            headers={"X-API-Key": api_key_value}
        )

        response = await client.get(
            "/api/v1/console/usage/stats",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "total_requests" in data
        assert "total_prompt_tokens" in data
        assert "total_completion_tokens" in data
        assert "total_cost" in data
        assert "by_agent" in data

    @pytest.mark.asyncio
    async def test_get_usage_stats_period(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test getting usage statistics for different periods."""
        for period in ["day", "week", "month"]:
            response = await client.get(
                f"/api/v1/console/usage/stats?period={period}",
                headers=auth_headers
            )
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_get_usage_logs(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test getting usage logs."""
        # Make a request first
        api_key_value = test_api_key[1]
        await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Test"}]
            },
            headers={"X-API-Key": api_key_value}
        )

        response = await client.get(
            "/api/v1/console/usage/logs",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_get_usage_logs_pagination(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test getting usage logs with pagination."""
        api_key_value = test_api_key[1]
        # Make multiple requests
        for _ in range(5):
            await client.post(
                "/v1/chat/completions",
                json={
                    "model": "asgard/code-refactor",
                    "messages": [{"role": "user", "content": "Test"}]
                },
                headers={"X-API-Key": api_key_value}
            )

        response = await client.get(
            "/api/v1/console/usage/logs?limit=2&offset=0",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) <= 2


class TestBalanceTransactions:
    """Test balance and transactions functionality."""

    @pytest.mark.asyncio
    async def test_balance_response_format(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_user: User
    ):
        """Test balance response has correct format."""
        response = await client.get(
            "/api/v1/console/balance",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "balance" in data
        assert "currency" in data
        assert data["currency"] == "Credit"


class TestRateLimiting:
    """Test rate limiting functionality."""

    @pytest.mark.asyncio
    async def test_rate_limit_default(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test default rate limit is applied."""
        api_key_obj, _ = test_api_key
        assert api_key_obj.rate_limit == 60  # Default

    @pytest.mark.asyncio
    async def test_rate_limit_custom(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User
    ):
        """Test custom rate limit is applied."""
        api_key_value, prefix = generate_api_key()
        custom_key = APIKey(
            key_hash=hash_api_key(api_key_value),
            key_prefix=prefix,
            name="Custom Rate Key",
            user_id=test_user.id,
            rate_limit=30,  # Custom rate limit
            is_active=True,
        )
        db_session.add(custom_key)
        await db_session.commit()

        # Verify rate limit is set
        assert custom_key.rate_limit == 30


class TestAPIKeyExpiration:
    """Test API key expiration functionality."""

    @pytest.mark.asyncio
    async def test_key_expiration_future(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_agents: list
    ):
        """Test key with future expiration works."""
        api_key_value, prefix = generate_api_key()
        future_key = APIKey(
            key_hash=hash_api_key(api_key_value),
            key_prefix=prefix,
            name="Future Key",
            user_id=test_user.id,
            expires_at=datetime.utcnow() + timedelta(days=30),
            is_active=True,
        )
        db_session.add(future_key)
        await db_session.commit()

        # Should work
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Test"}]
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_key_expiration_past(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_agents: list
    ):
        """Test key with past expiration fails."""
        api_key_value, prefix = generate_api_key()
        expired_key = APIKey(
            key_hash=hash_api_key(api_key_value),
            key_prefix=prefix,
            name="Expired Key",
            user_id=test_user.id,
            expires_at=datetime.utcnow() - timedelta(days=1),
            is_active=True,
        )
        db_session.add(expired_key)
        await db_session.commit()

        # Should fail
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Test"}]
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 403


class TestIPWhitelist:
    """Test IP whitelist functionality."""

    @pytest.mark.asyncio
    async def test_empty_whitelist_allows(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        test_agents: list
    ):
        """Test empty whitelist allows all IPs."""
        api_key_value, prefix = generate_api_key()
        key = APIKey(
            key_hash=hash_api_key(api_key_value),
            key_prefix=prefix,
            name="No Whitelist Key",
            user_id=test_user.id,
            ip_whitelist=[],  # Empty whitelist
            is_active=True,
        )
        db_session.add(key)
        await db_session.commit()

        # Should work (empty whitelist means all IPs allowed)
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Test"}]
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 200
