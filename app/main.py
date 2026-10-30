"""FastAPI application entry point."""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.config import settings
from app.database import SessionLocal
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.routers import auth, bookings, coupons, events, razorpay, reports, seats, tickets, venues, waitlist
from app.services.auth_service import ensure_initial_admin
from app.utils.errors import register_error_handlers
from app.websockets import seat_map
from app.websockets.manager import manager


logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app")

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager.start()
    try:
        db = SessionLocal()
        try:
            ensure_initial_admin(db)
        finally:
            db.close()
    except Exception:
        logger.exception("Initial admin setup failed (have you run `alembic upgrade head`?)")
    if settings.RUN_SCHEDULER:
        start_scheduler()
    yield
    stop_scheduler()
    await manager.stop()


app = FastAPI(
    title="Event & Movie Ticket Booking System",
    version="1.0.0",
    description="FastAPI + MySQL + Redis ticket booking backend. All times are UTC.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

for module in (auth, venues, events, seats, bookings, tickets, waitlist, coupons, reports, razorpay):
    app.include_router(module.router)
app.include_router(seat_map.router)


@app.get("/", tags=["Health"])
def root():
    return {"service": "Event & Movie Ticket Booking System", "docs": "/docs", "seat_map_test": "/seat-map-test"}


@app.get("/health", tags=["Health"])
def health():
    """Liveness + dependency check used by Docker health checks."""
    status = {"api": "ok"}
    try:
        from sqlalchemy import text

        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            status["database"] = "ok"
        finally:
            db.close()
    except Exception:
        status["database"] = "unavailable"
    try:
        from app.redis_client import get_redis

        get_redis().ping()
        status["redis"] = "ok"
    except Exception:
        status["redis"] = "unavailable"
    healthy = all(v == "ok" for v in status.values())
    return JSONResponse(status_code=200 if healthy else 503, content=status)


@app.get("/seat-map-test", include_in_schema=False)
def seat_map_test_page():
    return FileResponse(os.path.join(STATIC_DIR, "seat_map_test.html"), media_type="text/html")
