"""Redis Cache Service for Asgard API"""

import redis.asyncio as redis
from typing import Optional, Any
from datetime import timedelta
import json

from app.config import settings


class CacheService:
    """Redis-based cache service"""

    def __init__(self):
        self._client: Optional[redis.Redis] = None

    async def connect(self):
        """Initialize Redis connection"""
        if not settings.redis_url:
            return None
        try:
            self._client = redis.from_url(settings.redis_url, decode_responses=True)
            return self._client
        except Exception:
            return None

    async def close(self):
        """Close Redis connection"""
        if self._client:
            await self._client.close()
            self._client = None

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        if not self._client:
            return None
        try:
            value = await self._client.get(key)
            if value:
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return value
            return None
        except Exception:
            return None

    async def set(
        self,
        key: str,
        value: Any,
        expire_seconds: int = 300,
    ) -> bool:
        """Set value in cache with TTL"""
        if not self._client:
            return False
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            await self._client.setex(key, expire_seconds, value)
            return True
        except Exception:
            return False

    async def delete(self, key: str) -> bool:
        """Delete key from cache"""
        if not self._client:
            return False
        try:
            await self._client.delete(key)
            return True
        except Exception:
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists"""
        if not self._client:
            return False
        try:
            return await self._client.exists(key) > 0
        except Exception:
            return False

    # Agent-specific methods
    async def get_agent(self, agent_id: str) -> Optional[dict]:
        """Get agent info from cache"""
        return await self.get(f"agent:{agent_id}")

    async def set_agent(self, agent_id: str, data: dict, ttl: int = 300) -> bool:
        """Cache agent info"""
        return await self.set(f"agent:{agent_id}", data, ttl)

    async def invalidate_agent(self, agent_id: str) -> bool:
        """Invalidate agent cache"""
        return await self.delete(f"agent:{agent_id}")

    async def get_agents_list(self) -> Optional[list]:
        """Get cached agents list"""
        return await self.get("agents:list")

    async def set_agents_list(self, agents: list, ttl: int = 60) -> bool:
        """Cache agents list (shorter TTL for list)"""
        return await self.set("agents:list", agents, ttl)


# Global cache instance
cache = CacheService()


async def init_cache():
    """Initialize cache on startup"""
    await cache.connect()


async def close_cache():
    """Close cache on shutdown"""
    await cache.close()
