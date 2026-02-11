"""Ollama LLM Provider for Asgard Agent Engine"""

import json
import asyncio
import httpx
from typing import AsyncGenerator, Dict, Any, List
from app.config import settings


class OllamaProvider:
    """Ollama LLM provider for local model inference"""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5",
        timeout: int = 120,
    ):
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self._client: httpx.AsyncClient = None

    async def connect(self):
        """Initialize HTTP client"""
        self._client = httpx.AsyncClient(timeout=self.timeout)

    async def close(self):
        """Close HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = True,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Send chat request to Ollama
        """
        if not self._client:
            await self.connect()

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
            ) as response:
                async for line in response.aiter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            yield data
                        except json.JSONDecodeError:
                            continue
        except httpx.ConnectError:
            yield {
                "error": {
                    "message": f"Cannot connect to Ollama at {self.base_url}",
                    "type": "connection_error"
                }
            }
        except Exception as e:
            yield {"error": {"message": str(e), "type": "server_error"}}

    async def chat_once(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """
        Send non-streaming chat request to Ollama
        """
        if not self._client:
            await self.connect()

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        try:
            response = await self._client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            data = response.json()
            return data.get("message", {}).get("content", "")
        except Exception as e:
            return f"Error: {str(e)}"

    async def list_models(self) -> List[str]:
        """List available models in Ollama"""
        if not self._client:
            await self.connect()

        try:
            response = await self._client.get(f"{self.base_url}/api/tags")
            data = response.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    async def is_available(self) -> bool:
        """Check if Ollama is running"""
        try:
            if not self._client:
                await self.connect()
            response = await self._client.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except Exception:
            return False


_ollama_provider: OllamaProvider = None


def get_ollama_provider() -> OllamaProvider:
    """Get or create Ollama provider instance"""
    global _ollama_provider
    if _ollama_provider is None:
        _ollama_provider = OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
        )
    return _ollama_provider


async def init_ollama():
    """Initialize Ollama connection"""
    provider = get_ollama_provider()
    await provider.connect()
    return provider


async def close_ollama():
    """Close Ollama connection"""
    global _ollama_provider
    if _ollama_provider:
        await _ollama_provider.close()
        _ollama_provider = None
