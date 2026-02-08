"""
Chat Completions API tests for Asgard API.
"""
import pytest
import json
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, APIKey, Agent


class TestChatCompletionBasic:
    """Test basic chat completion functionality."""

    @pytest.mark.asyncio
    async def test_chat_completion_success(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test successful chat completion."""
        api_key_value = test_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Hello, how are you?"}
                ]
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["object"] == "chat.completion"
        assert data["model"] == "asgard/code-refactor"
        assert len(data["choices"]) == 1
        assert data["choices"][0]["index"] == 0
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert data["choices"][0]["message"]["content"] is not None
        assert data["choices"][0]["finish_reason"] == "stop"
        assert "usage" in data
        assert "prompt_tokens" in data["usage"]
        assert "completion_tokens" in data["usage"]
        assert "total_tokens" in data["usage"]

    @pytest.mark.asyncio
    async def test_chat_completion_with_temperature(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test chat completion with custom temperature."""
        api_key_value = test_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Test"}],
                "temperature": 0.5
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_chat_completion_with_max_tokens(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test chat completion with max_tokens."""
        api_key_value = test_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Test"}],
                "max_tokens": 100
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 200
        data = response.json()
        # Total tokens should be reasonable
        assert data["usage"]["total_tokens"] > 0

    @pytest.mark.asyncio
    async def test_chat_completion_user_content_only(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test chat completion with user message only."""
        api_key_value = test_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Write a function."}]
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 200


class TestChatCompletionStreaming:
    """Test streaming chat completion functionality."""

    @pytest.mark.asyncio
    async def test_chat_completion_streaming(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test streaming chat completion."""
        api_key_value = test_api_key[1]
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True
            },
            headers={"X-API-Key": api_key_value}
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

            chunks = []
            async for chunk in response.aiter_lines():
                if chunk.startswith("data: "):
                    data = chunk[6:]
                    if data == "[DONE]":
                        break
                    chunks.append(json.loads(data))

            # Should have received at least one chunk
            assert len(chunks) > 0
            # First chunk should have proper structure
            assert "id" in chunks[0]
            assert "object" in chunks[0]
            assert "choices" in chunks[0]

    @pytest.mark.asyncio
    async def test_chat_completion_streaming_hanhan_style(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test streaming chat completion with Han Han style agent."""
        api_key_value = test_api_key[1]
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "asgard/hanhan-style",
                "messages": [{"role": "user", "content": "Write about life."}],
                "stream": True
            },
            headers={"X-API-Key": api_key_value}
        ) as response:
            assert response.status_code == 200

            chunks = []
            async for chunk in response.aiter_lines():
                if chunk.startswith("data: "):
                    data = chunk[6:]
                    if data == "[DONE]":
                        break
                    chunks.append(json.loads(data))

            assert len(chunks) > 0


class TestChatCompletionErrors:
    """Test chat completion error handling."""

    @pytest.mark.asyncio
    async def test_chat_invalid_model(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test chat completion with invalid model."""
        api_key_value = test_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/invalid-model",
                "messages": [{"role": "user", "content": "Test"}]
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_chat_inactive_model(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test chat completion with inactive model."""
        api_key_value = test_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/unit-test",  # This is inactive
                "messages": [{"role": "user", "content": "Test"}]
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 403
        assert "not available" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_chat_missing_messages(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str]
    ):
        """Test chat completion with missing messages field."""
        api_key_value = test_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor"
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_missing_model(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str]
    ):
        """Test chat completion with missing model field."""
        api_key_value = test_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "Test"}]
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_empty_messages(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str]
    ):
        """Test chat completion with empty messages list."""
        api_key_value = test_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": []
            },
            headers={"X-API-Key": api_key_value}
        )
        # Should still work (agent will handle empty messages)
        assert response.status_code == 200


class TestChatCompletionQuotaTracking:
    """Test quota tracking in chat completions."""

    @pytest.mark.asyncio
    async def test_quota_deducted(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        db_session: AsyncSession,
        test_agents: list
    ):
        """Test that quota is deducted after chat completion."""
        api_key_obj, api_key_value = test_api_key
        initial_used = api_key_obj.used_quota

        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Test message"}]
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 200

        # Refresh and check quota
        await db_session.refresh(api_key_obj)
        assert api_key_obj.used_quota > initial_used

    @pytest.mark.asyncio
    async def test_quota_exceeded(
        self,
        client: AsyncClient,
        test_api_key_low_quota: tuple[APIKey, str],
        test_agents: list
    ):
        """Test quota exceeded error handling."""
        api_key_obj, api_key_value = test_api_key_low_quota

        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Test message"}]
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 429
        assert "quota" in response.json()["detail"].lower()


class TestChatCompletionUsage:
    """Test usage log generation."""

    @pytest.mark.asyncio
    async def test_usage_log_generated(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        db_session: AsyncSession,
        test_agents: list
    ):
        """Test that usage log is generated after chat completion."""
        from app.models import UsageLog
        api_key_obj, api_key_value = test_api_key

        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Test"}]
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 200

        # Check that usage log was created
        result = await db_session.execute(
            select(UsageLog).where(UsageLog.api_key_id == api_key_obj.id)
        )
        logs = result.scalars().all()
        assert len(logs) > 0

    @pytest.mark.asyncio
    async def test_usage_log_success_status(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        db_session: AsyncSession,
        test_agents: list
    ):
        """Test that usage log has success status."""
        from app.models import UsageLog
        api_key_obj, api_key_value = test_api_key

        await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Test"}]
            },
            headers={"X-API-Key": api_key_value}
        )

        result = await db_session.execute(
            select(UsageLog).where(UsageLog.api_key_id == api_key_obj.id)
        )
        logs = result.scalars().all()
        if logs:
            assert logs[-1].status == "success"


class TestChatCompletionParameterValidation:
    """Test parameter validation in chat completions."""

    @pytest.mark.asyncio
    async def test_temperature_range_low(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test temperature below minimum (0)."""
        api_key_value = test_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Test"}],
                "temperature": -0.1
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_temperature_range_high(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test temperature above maximum (2)."""
        api_key_value = test_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Test"}],
                "temperature": 2.1
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_max_tokens_range_low(
        self,
        client: AsyncClient,
        test_api_key: tuple[APIKey, str],
        test_agents: list
    ):
        """Test max_tokens below minimum (1)."""
        api_key_value = test_api_key[1]
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "asgard/code-refactor",
                "messages": [{"role": "user", "content": "Test"}],
                "max_tokens": 0
            },
            headers={"X-API-Key": api_key_value}
        )
        assert response.status_code == 422


# Helper import for tests using select
from sqlalchemy import select
