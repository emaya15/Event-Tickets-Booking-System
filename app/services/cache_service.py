"""Small Redis JSON cache used for event listings (the live seat map is NEVER cached)."""
import json
import logging
from typing import Any, Optional

from redis.exceptions import RedisError

from app.config import settings
from app.redis_client import get_redis

logger = logging.getLogger("app.cache")
PREFIX = "cache:events:"


def cache_get(key: str) -> Optional[Any]:
    try:
        raw = get_redis().get(PREFIX + key)
        return json.loads(raw) if raw else None
    except (RedisError, ValueError) as exc:  # cache problems must never break a request
        logger.warning("cache_get failed: %s", exc)
        return None


def cache_set(key: str, value: Any, ttl: Optional[int] = None) -> None:
    try:
        get_redis().set(PREFIX + key, json.dumps(value, default=str), ex=ttl or settings.EVENT_CACHE_SECONDS)
    except (RedisError, TypeError) as exc:
        logger.warning("cache_set failed: %s", exc)


def invalidate_event_cache() -> None:
    """Drop every cached event list / detail (called when events or shows change)."""
    try:
        r = get_redis()
        keys = list(r.scan_iter(match=PREFIX + "*", count=200))
        if keys:
            r.delete(*keys)
    except RedisError as exc:
        logger.warning("cache invalidation failed: %s", exc)
