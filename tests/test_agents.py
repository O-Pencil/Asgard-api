"""
Agent Management API tests for Asgard API.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Agent


class TestAgentListing:
    """Test agent listing functionality."""

    @pytest.mark.asyncio
    async def test_list_agents(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_agents: list
    ):
        """Test listing all agents."""
        response = await client.get("/api/v1/agents", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "agents" in data
        assert "total" in data
        assert isinstance(data["agents"], list)
        # Should have all active public agents
        assert data["total"] >= 3  # 3 active agents created

    @pytest.mark.asyncio
    async def test_list_agents_pagination(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_agents: list
    ):
        """Test agent listing with pagination."""
        response = await client.get(
            "/api/v1/agents?page=1&page_size=2",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["agents"]) <= 2

    @pytest.mark.asyncio
    async def test_list_agents_second_page(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_agents: list
    ):
        """Test agent listing second page."""
        response = await client.get(
            "/api/v1/agents?page=2&page_size=2",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        # Second page may be empty
        assert isinstance(data["agents"], list)

    @pytest.mark.asyncio
    async def test_list_agents_filter_by_category(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_agents: list
    ):
        """Test agent listing filtered by category."""
        response = await client.get(
            "/api/v1/agents?category=development",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        for agent in data["agents"]:
            assert agent["category"] == "development"

    @pytest.mark.asyncio
    async def test_list_agents_filter_by_search(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_agents: list
    ):
        """Test agent listing with search filter."""
        response = await client.get(
            "/api/v1/agents?search=code",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        # Should find agents matching "code"
        for agent in data["agents"]:
            assert "code" in agent["name"].lower() or "code" in (agent["description"] or "").lower()

    @pytest.mark.asyncio
    async def test_list_agents_without_auth(self, client: AsyncClient):
        """Test agent listing without authentication."""
        response = await client.get("/api/v1/agents")
        assert response.status_code == 401


class TestAgentDetails:
    """Test agent details functionality."""

    @pytest.mark.asyncio
    async def test_get_agent_details(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_agents: list
    ):
        """Test getting agent details."""
        agent = test_agents[0]  # code-refactor agent
        response = await client.get(
            f"/api/v1/agents/{agent.agent_id}",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == agent.agent_id
        assert data["name"] == agent.name
        assert data["description"] == agent.description
        assert data["category"] == agent.category
        assert data["capabilities"] == agent.capabilities
        assert data["is_active"] is True

    @pytest.mark.asyncio
    async def test_get_nonexistent_agent(
        self,
        client: AsyncClient,
        auth_headers: dict
    ):
        """Test getting details for non-existent agent."""
        response = await client.get(
            "/api/v1/agents/asgard/nonexistent",
            headers=auth_headers
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_inactive_agent(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_agents: list
    ):
        """Test getting details for inactive agent."""
        # Find inactive agent
        inactive_agent = next((a for a in test_agents if not a.is_active), None)
        if inactive_agent:
            response = await client.get(
                f"/api/v1/agents/{inactive_agent.agent_id}",
                headers=auth_headers
            )
            # Should return 404 (not available)
            assert response.status_code == 404


class TestAgentEnableDisable:
    """Test agent enable/disable functionality."""

    @pytest.mark.asyncio
    async def test_enable_agent(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_agents: list
    ):
        """Test enabling an agent."""
        agent = test_agents[0]
        response = await client.post(
            f"/api/v1/agents/{agent.agent_id}/enable",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "enabled"
        assert data["agent_id"] == agent.agent_id

    @pytest.mark.asyncio
    async def test_disable_agent(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_agents: list
    ):
        """Test disabling an agent."""
        agent = test_agents[0]
        response = await client.post(
            f"/api/v1/agents/{agent.agent_id}/disable",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "disabled"
        assert data["agent_id"] == agent.agent_id

    @pytest.mark.asyncio
    async def test_enable_nonexistent_agent(
        self,
        client: AsyncClient,
        auth_headers: dict
    ):
        """Test enabling non-existent agent."""
        response = await client.post(
            "/api/v1/agents/asgard/nonexistent/enable",
            headers=auth_headers
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_disable_nonexistent_agent(
        self,
        client: AsyncClient,
        auth_headers: dict
    ):
        """Test disabling non-existent agent."""
        response = await client.post(
            "/api/v1/agents/asgard/nonexistent/disable",
            headers=auth_headers
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_enable_agent_without_auth(self, client: AsyncClient):
        """Test enabling agent without authentication."""
        response = await client.post("/api/v1/agents/asgard/code-refactor/enable")
        assert response.status_code == 401


class TestModelsEndpoint:
    """Test OpenAI-compatible models endpoint."""

    @pytest.mark.asyncio
    async def test_list_models(self, client: AsyncClient):
        """Test listing models (OpenAI compatible)."""
        response = await client.get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)
        # Should contain the hardcoded models
        model_ids = [m["id"] for m in data["data"]]
        assert "asgard/code-refactor" in model_ids
        assert "asgard/hanhan-style" in model_ids

    @pytest.mark.asyncio
    async def test_models_no_auth_required(self, client: AsyncClient):
        """Test that models endpoint doesn't require authentication."""
        response = await client.get("/v1/models")
        assert response.status_code == 200
