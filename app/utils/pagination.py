"""Shared pagination helpers."""
from typing import Any, Sequence

from fastapi import Query


class Pagination:
    """Dependency that validates ``skip`` and ``limit`` query parameters."""

    def __init__(
        self,
        skip: int = Query(0, ge=0, description="Number of records to skip"),
        limit: int = Query(10, ge=1, le=100, description="Page size (1-100)"),
    ):
        self.skip = skip
        self.limit = limit


def page(items: Sequence[Any], total: int, p: Pagination) -> dict:
    """Predictable paginated response envelope."""
    return {"items": list(items), "total": total, "skip": p.skip, "limit": p.limit}
