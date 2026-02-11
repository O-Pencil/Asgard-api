"""Rate Limiting Middleware for Asgard API"""

from datetime import datetime, timedelta
from typing import Dict, Tuple
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse

from app.config import settings


class RateLimiter:
    """Simple in-memory rate limiter"""

    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        self._requests: Dict[str, list] = {}  # key -> [timestamps]

    def _cleanup_old_requests(self, key: str, now: datetime):
        """Remove requests older than 1 minute"""
        cutoff = now - timedelta(minutes=1)
        if key in self._requests:
            self._requests[key] = [
                ts for ts in self._requests[key] if ts > cutoff
            ]

    def _get_client_key(self, request: Request) -> str:
        """Get client identifier for rate limiting"""
        # Try to get real IP from X-Forwarded-For header
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def is_allowed(self, request: Request) -> Tuple[bool, int, int]:
        """
        Check if request is allowed.

        Returns:
            Tuple of (allowed, remaining_requests, reset_time_seconds)
        """
        client_key = self._get_client_key(request)
        now = datetime.now()

        # Cleanup old requests
        self._cleanup_old_requests(client_key, now)

        # Get current request count
        current_count = len(self._requests.get(client_key, []))

        # Check limit
        if current_count >= self.requests_per_minute:
            # Calculate reset time
            if client_key in self._requests and self._requests[client_key]:
                oldest = min(self._requests[client_key])
                reset_after = 60 - (now - oldest).seconds
            else:
                reset_after = 60
            return False, 0, reset_after

        # Record this request
        if client_key not in self._requests:
            self._requests[client_key] = []
        self._requests[client_key].append(now)

        remaining = self.requests_per_minute - current_count - 1
        return True, remaining, 60


# Global rate limiter
rate_limiter = RateLimiter(
    requests_per_minute=settings.rate_limit_per_minute
)


async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting middleware"""
    # Skip rate limiting for health check
    if request.url.path == "/health":
        return await call_next(request)

    # Check rate limit
    allowed, remaining, reset_after = rate_limiter.is_allowed(request)

    if not allowed:
        return JSONResponse(
            status_code=429,
            headers={
                "X-RateLimit-Remaining": str(remaining),
                "X-RateLimit-Reset-After": str(reset_after),
                "Retry-After": str(reset_after),
            },
            content={
                "detail": "Rate limit exceeded",
                "message": f"Too many requests. Please retry after {reset_after} seconds.",
            },
        )

    # Add rate limit headers to response
    response = await call_next(request)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


def get_rate_limit_headers(remaining: int, reset_after: int) -> dict:
    """Get rate limit headers"""
    return {
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset-After": str(reset_after),
    }
