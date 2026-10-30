"""APScheduler setup.

Multiple API workers: every worker would start its own scheduler. To stay safe we (a) use a Redis
lock per job run so only one process executes a given tick, and (b) recommend running the scheduler
in ONE dedicated process (RUN_SCHEDULER=false on API workers, `python -m app.jobs.run_scheduler`).
"""
import logging
import uuid

from apscheduler.schedulers.background import BackgroundScheduler
from redis.exceptions import RedisError

from app.jobs import tasks
from app.redis_client import get_redis

logger = logging.getLogger("app.scheduler")
_scheduler = None


def _guarded(name: str, fn, ttl: int):
    """Wrap a job so that only one process runs it per interval (Redis SET NX)."""

    def run():
        try:
            if not get_redis().set(f"jobs:lock:{name}", uuid.uuid4().hex, nx=True, ex=ttl):
                return
        except RedisError:
            logger.warning("Redis unavailable; skipping job %s", name)
            return
        fn()

    run.__name__ = f"guarded_{name}"
    return run


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    sched = BackgroundScheduler(timezone="UTC")
    jobs = [
        ("expire_bookings", tasks.expire_pending_bookings, {"minutes": 1}, 50),
        ("show_reminders", tasks.send_show_reminders, {"minutes": 30}, 25 * 60),
        ("complete_shows", tasks.complete_shows, {"minutes": 15}, 12 * 60),
        ("complete_events", tasks.complete_events, {"days": 1}, 23 * 3600),
    ]
    for name, fn, interval, ttl in jobs:
        sched.add_job(
            _guarded(name, fn, ttl), "interval", id=name, max_instances=1, coalesce=True, replace_existing=True, **interval
        )
    sched.start()
    _scheduler = sched
    logger.info("Scheduler started with %d jobs", len(jobs))
    return sched


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")
