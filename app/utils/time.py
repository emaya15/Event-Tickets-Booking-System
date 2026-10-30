"""All timestamps in the database are naive UTC datetimes."""
from datetime import datetime, timezone


def utc_now() -> datetime:
    """Current UTC time as a naive datetime (matches how MySQL DATETIME is stored)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt):
    """ISO-8601 string with a trailing Z for naive-UTC datetimes."""
    return dt.isoformat() + "Z" if dt else None
