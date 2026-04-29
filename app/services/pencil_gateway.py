"""
Pencil Agent Gateway Backend Service

[WHO]  Provides PencilAgentBackend class for proxying requests to Pencil Agent Gateway
[FROM] Depends on app.config.Settings, httpx
[TO]   Consumed by app.routers.chat, app.routers.agents
[HERE] app/services/pencil_gateway.py within Asgard API backend
"""

import logging
import uuid
from typing import Optional

import httpx
from fastapi import Request
from fastapi.responses import StreamingResponse, JSONResponse
from starlette.background import BackgroundTask

from app.config import Settings

logger = logging.getLogger(__name__)


def _new_request_id() -> str:
    return str(uuid.uuid4())


class PencilAgentBackend:
    """
    HTTP proxy to Pencil Agent Gateway.

    Asgard does NOT import Gateway code or nano-pencil SDK.
    All communication is via httpx AsyncClient over internal network.
    """

    def __init__(self, settings: Settings):
        self.internal_key = settings.pencil_gateway_internal_key
        self.client = httpx.AsyncClient(
            base_url=settings.pencil_gateway_url,
            timeout=httpx.Timeout(
                connect=settings.pencil_gateway_connect_timeout_s,
                read=settings.pencil_gateway_read_timeout_s,
                write=30.0,
                pool=30.0,
            ),
        )
        logger.info(
            "PencilAgentBackend initialized",
            extra={"gateway_url": settings.pencil_gateway_url},
        )

    # ------------------------------------------------------------------
    # Headers
    # ------------------------------------------------------------------

    def _build_headers(
        self, request: Request, user, gateway_agent_id: str
    ) -> dict[str, str]:
        request_id = request.headers.get("x-request-id") or _new_request_id()
        return {
            "Authorization": f"Bearer {self.internal_key}",
            "Content-Type": "application/json",
            "X-Request-Id": request_id,
            "X-Asgard-User": str(user.uuid),
            "X-Asgard-Agent": gateway_agent_id,
        }

    # ------------------------------------------------------------------
    # Create / sync agent on Gateway
    # ------------------------------------------------------------------

    async def create_agent(self, request: Request, user, agent) -> dict:
        """
        POST /v1/agents to Gateway to create or update a PencilAgent instance.

        Args:
            request: Incoming FastAPI request (for header forwarding).
            user: User ORM object (must have .uuid).
            agent: Agent ORM object (must have .name, .parameters).

        Returns:
            Gateway JSON response body.
        """
        params = agent.parameters or {}
        gateway_agent_id = params.get("gateway_agent_id", agent.agent_id.replace("pencil/", ""))

        body = {
            "id": gateway_agent_id,
            "name": agent.name,
            "soul": params.get("soul") or {},
            "memory": params.get("memory") or {"mode": "short-term", "maxTurns": 30},
            "model": params.get("model") or {},
            "engine": {"type": "nano-pencil"},
        }

        headers = self._build_headers(request, user, gateway_agent_id)

        res = await self.client.post(
            "/v1/agents",
            json=body,
            headers=headers,
        )
        res.raise_for_status()
        return res.json()

    # ------------------------------------------------------------------
    # Update agent on Gateway (PUT — preserves running sessions)
    # ------------------------------------------------------------------

    async def update_agent(self, request: Request, user, agent) -> dict:
        """
        PUT /v1/agents/:id on Gateway. Differs from create_agent (POST):
        Gateway keeps existing in-memory sessions, so the user's running chat
        history survives a Soul/model edit.

        New sessions created after the update see the new config; sessions
        already open keep their captured Soul. UI should warn users that
        starting a fresh conversation is required to fully apply the change.
        """
        params = agent.parameters or {}
        gateway_agent_id = params.get("gateway_agent_id", agent.agent_id.replace("pencil/", ""))

        body = {
            "id": gateway_agent_id,
            "name": agent.name,
            "soul": params.get("soul") or {},
            "memory": params.get("memory") or {"mode": "short-term", "maxTurns": 30},
            "model": params.get("model") or {},
            "engine": {"type": "nano-pencil"},
        }

        headers = self._build_headers(request, user, gateway_agent_id)

        res = await self.client.put(
            f"/v1/agents/{gateway_agent_id}",
            json=body,
            headers=headers,
        )
        res.raise_for_status()
        return res.json()

    # ------------------------------------------------------------------
    # Proxy chat completion
    # ------------------------------------------------------------------

    async def proxy_chat(
        self, request: Request, body: dict, user, agent
    ):
        """
        Proxy /v1/chat/completions to Gateway.

        - Streaming: uses aiter_raw() + StreamingResponse (no EventSourceResponse re-wrap).
        - Non-streaming: returns JSONResponse with upstream status code preserved.

        Args:
            request: Incoming FastAPI request.
            body: Serialized ChatCompletionRequest dict.
            user: User ORM object.
            agent: Agent ORM object.

        Returns:
            StreamingResponse or JSONResponse.
        """
        params = agent.parameters or {}
        gateway_agent_id = params.get("gateway_agent_id", agent.agent_id.replace("pencil/", ""))
        body["model"] = f"pencil/{gateway_agent_id}"

        headers = self._build_headers(request, user, gateway_agent_id)

        if body.get("stream"):
            return await self._proxy_stream(body, headers)
        else:
            return await self._proxy_non_stream(body, headers)

    async def _proxy_stream(self, body: dict, headers: dict) -> StreamingResponse:
        """
        Stream proxy using aiter_raw() — preserves Gateway SSE bytes as-is.
        """
        upstream = await self.client.send(
            self.client.build_request(
                "POST",
                "/v1/chat/completions",
                json=body,
                headers=headers,
            ),
            stream=True,
        )

        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Request-Id": headers.get("X-Request-Id", ""),
            },
            background=BackgroundTask(upstream.aclose),
        )

    async def _proxy_non_stream(self, body: dict, headers: dict) -> JSONResponse:
        """
        Non-stream proxy — preserves upstream status code.
        """
        upstream = await self.client.post(
            "/v1/chat/completions",
            json=body,
            headers=headers,
        )
        try:
            payload = upstream.json()
        except Exception:
            payload = {"error": {"message": upstream.text or "Gateway returned non-JSON"}}

        # Preserve upstream status code (404 stays 404, etc.)
        status_code = upstream.status_code

        # Special handling: 401 from Gateway = config error, don't expose to user
        if status_code == 401:
            logger.error(
                "Gateway returned 401 — internal key may be misconfigured",
                extra={"request_id": headers.get("X-Request-Id")},
            )
            payload = {"error": {"message": "Internal gateway configuration error", "type": "server_error"}}
            status_code = 502  # Bad Gateway

        return JSONResponse(payload, status_code=status_code)
