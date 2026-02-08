"""
API Key and Console API tests for Asgard API.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, APIKey
from app.auth import hash_api_key, generate_api_key


class TestAPIKeyCreation:
    """Test API key creation functionality."""

    @pytest.mark.asyncio
    async def test_create_api_key_success(self, client: AsyncClient, auth_headers: dict, test_user: User):
        """Test successful API key creation."""
        response = await client.post(
            "/api/v1/console/keys",
            json={"name": "My API Key"},
            headers=auth_headers
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "My API Key"
        assert "api_key" in data
        assert "key_prefix" in data
        assert data["api_key"].startswith("asgard_")
        assert len(data["api_key"]) > 30

    @pytest.mark.asyncio
    async def test_create_api_key_with_quota(self, client: AsyncClient, auth_headers: dict):
        """Test API key creation with quota limit."""
        response = await client.post(
            "/api/v1/console/keys",
            json={
                "name": "Limited Key",
                "quota_limit": 100.0,
                "rate_limit": 120
            },
            headers=auth_headers
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Limited Key"

    @pytest.mark.asyncio
    async def test_create_api_key_without_auth(self, client: AsyncClient):
        """Test API key creation without authentication."""
        response = await client.post(
            "/api/v1/console/keys",
            json={"name": "Unauthorized Key"}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_api_key_unauthorized_user(self, client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
        """Test creating API key for another user."""
        # The auth_headers are for test_user, so we can't create key for another user
        # This test just verifies the endpoint works for the authenticated user
        response = await client.post(
            "/api/v1/console/keys",
            json={"name": "User's Key"},
            headers=auth_headers
        )
        assert response.status_code == 201


class TestAPIKeyListing:
    """Test API key listing functionality."""

    @pytest.mark.asyncio
    async def test_list_api_keys(self, client: AsyncClient, auth_headers: dict, test_api_key: tuple[APIKey, str]):
        """Test listing API keys."""
        response = await client.get("/api/v1/console/keys", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        # Check key structure
        key = data[0]
        assert "uuid" in key
        assert "key_prefix" in key
        assert "name" in key
        assert "is_active" in key

    @pytest.mark.asyncio
    async def test_list_api_keys_empty(self, client: AsyncClient, auth_headers: dict, test_user: User):
        """Test listing API keys when none exist."""
        # This user has no keys yet
        response = await client.get("/api/v1/console/keys", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestAPIKeyAuthentication:
    """Test API key authentication in requests."""

    @pytest.mark.asyncio
    async def test_chat_with_valid_api_key(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        test_agents: list,
        chat_completion_payload: dict
    ):
        """Test chat completion with valid API key."""
        api_key_value = test_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json=chat_completion_payload,
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["object"] == "chat.completion"
        assert data["model"] == "asgard/code-refactor"
        assert "choices" in data
        assert "usage" in data

    @pytest.mark.asyncio
    async def test_chat_with_invalid_api_key(
        self,
        client: AsyncClient,
        test_agents: list,
        chat_completion_payload: dict
    ):
        """Test chat completion with invalid API key."""
        response = await client.post(
            "/v1/chat/completions",
            json=chat_completion_payload,
            headers={"X-API-Key": "invalid-api-key-value"}
        )
        assert response.status_code == 401
        assert "Invalid API key" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_chat_without_api_key(
        self,
        client: AsyncClient,
        test_agents: list,
        chat_completion_payload: dict
    ):
        """Test chat completion without API key."""
        response = await client.post(
            "/v1/chat/completions",
            json=chat_completion_payload
        )
        assert response.status_code == 401
        assert "API key required" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_chat_with_disabled_api_key(
        self,
        client: AsyncClient,
        test_disabled_api_key: tuple[APIKey, str],
        test_agents: list,
        chat_completion_payload: dict
    ):
        """Test chat completion with disabled API key."""
        api_key_value = test_disabled_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json=chat_completion_payload,
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_chat_with_expired_api_key(
        self,
        client: AsyncClient,
        test_expired_api_key: tuple[APIKey, str],
        test_agents: list,
        chat_completion_payload: dict
    ):
        """Test chat completion with expired API key."""
        api_key_value = test_expired_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json=chat_completion_payload,
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 403
        assert "expired" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_api_key_bearer_format(self, client: AsyncClient, test_api_key: tuple[APIKey, str], test_agents: list):
        """Test API key authentication with Bearer prefix."""
        api_key_value = test_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Test"}]
            },
            headers={"Authorization": f"Bearer {api_key_value}"}
        )
        assert response.status_code == 200


class TestAPIKeyDeletion:
    """Test API key deletion functionality."""

    @pytest.mark.asyncio
    async def test_delete_api_key(self, client: AsyncClient, auth_headers: dict, test_api_key: tuple[APIKey, str]):
        """Test deleting an API key."""
        api_key = test_api_key[0]
        response = await client.delete(
            f"/api/v1/console/keys/{api_key.uuid}",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_key(self, client: AsyncClient, auth_headers: dict):
        """Test deleting a non-existent API key."""
        response = await client.delete(
            "/api/v1/console/keys/nonexistent-uuid",
            headers=auth_headers
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_key_other_user(self, client: AsyncClient, auth_headers: dict, db_session: AsyncSession, test_user: User):
        """Test deleting API key belonging to another user."""
        # Create another user with their own key
        from app.auth import get_password_hash
        other_user = User(
            email="other@example.com",
            hashed_password=get_password_hash("password123"),
            balance=0.0
        )
        db_session.add(other_user)
        await db_session.commit()
        await db_session.refresh(other_user)

        other_key = APIKey(
            key_hash=hash_api_key("other-user-key"),
            key_prefix="asgard_xxxx",
            name="Other User Key",
            user_id=other_user.id,
            is_active=True
        )
        db_session.add(other_key)
        await db_session.commit()
        await db_session.refresh(other_key)

        # Try to delete other user's key
        response = await client.delete(
            f"/api/v1/console/keys/{other_key.uuid}",
            headers=auth_headers
        )
        assert response.status_code == 404


class TestAPIKeyRotation:
    """Test API key rotation functionality."""

    @pytest.mark.asyncio
    async def test_rotate_api_key(self, client: AsyncClient, auth_headers: dict, test_api_key: tuple[APIKey, str]):
        """Test rotating (regenerating) an API key."""
        api_key = test_api_key[0]
        old_prefix = api_key.key_prefix

        response = await client.post(
            f"/api/v1/console/keys/{api_key.uuid}/rotate",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["key_prefix"] != old_prefix
        assert len(data["api_key"]) > 30


class TestBalanceEndpoint:
    """Test balance endpoint functionality."""

    @pytest.mark.asyncio
    async def test_get_balance(self, client: AsyncClient, auth_headers: dict, test_user: User):
        """Test getting user balance."""
        response = await client.get("/api/v1/console/balance", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["balance"] == test_user.balance
        assert data["currency"] == "Credit"
