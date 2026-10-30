"""Redis-backed fixed-window rate limiter (shared by every application instance)."""
import logging

from fastapi import Depends, Request
from redis.exceptions import RedisError

from app.auth.dependencies import get_current_user
from app.config import settings
from app.models import User
from app.redis_client import get_redis
from app.utils.errors import RateLimited

logger = logging.getLogger("app.rate_limit")


def hit(key: str, limit: int, window_seconds: int) -> None:
    """Count one request for ``key``; raise RateLimited (429 + Retry-After) when over the limit."""
    r = get_redis()
    redis_key = f"ratelimit:{key}"
    count = r.incr(redis_key)
    ttl = r.ttl(redis_key)
    if count == 1 or ttl < 0:  # first hit (or a key that somehow lost its TTL) starts the window
        r.expire(redis_key, window_seconds)
        ttl = window_seconds
    if count > limit:
        retry_after = max(int(ttl), 1)
        raise RateLimited(
            f"Too many requests. Try again in {retry_after} seconds.",
            extra={"retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after)},
        )


def client_ip(request: Request) -> str:
    # Behind a reverse proxy, run uvicorn with --proxy-headers so request.client is the real IP.
    return request.client.host if request.client else "unknown"


def login_rate_limit(request: Request) -> None:
    """Dependency: max LOGIN_RATE_LIMIT login attempts per minute per IP."""
    hit(f"login:{client_ip(request)}", settings.LOGIN_RATE_LIMIT, settings.RATE_LIMIT_WINDOW_SECONDS)


def lock_rate_limit(user: User = Depends(get_current_user)) -> User:
    """Dependency: max LOCK_RATE_LIMIT seat-lock requests per minute per customer."""
    hit(f"lock:user:{user.id}", settings.LOCK_RATE_LIMIT, settings.RATE_LIMIT_WINDOW_SECONDS)
    return user
