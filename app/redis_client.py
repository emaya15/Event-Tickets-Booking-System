"""Redis connection helper (redis-py)."""
import redis

from app.config import settings

_client = None


def get_redis() -> "redis.Redis":
    """Return the shared Redis client (created lazily)."""
    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def set_redis(client) -> None:
    """Replace the shared client (used by tests to inject fakeredis)."""
    global _client
    _client = client
