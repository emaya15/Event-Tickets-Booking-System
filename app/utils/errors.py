"""Application errors and the consistent JSON error format.

Every error response looks like:  {"detail": "<message>", "code": "<machine_code>", ...extra}
"""
import logging
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.errors")


class AppError(Exception):
    """Base class for expected, client-facing errors."""

    status_code = 400
    code = "bad_request"

    def __init__(self, detail: str, extra: Optional[dict] = None, headers: Optional[dict] = None):
        super().__init__(detail)
        self.detail = detail
        self.extra = extra or {}
        self.headers = headers


class BadRequest(AppError):
    status_code, code = 400, "bad_request"


class Unauthorized(AppError):
    status_code, code = 401, "unauthorized"


class Forbidden(AppError):
    status_code, code = 403, "forbidden"


class NotFound(AppError):
    status_code, code = 404, "not_found"


class Conflict(AppError):
    status_code, code = 409, "conflict"


class Gone(AppError):
    status_code, code = 410, "gone"


class RateLimited(AppError):
    status_code, code = 429, "rate_limited"


class ServiceUnavailable(AppError):
    status_code, code = 503, "service_unavailable"


def _body(detail: str, code: str, extra: Optional[dict] = None) -> dict:
    body: dict[str, Any] = {"detail": detail, "code": code}
    if extra:
        body.update(extra)
    return body


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code, content=_body(exc.detail, exc.code, exc.extra), headers=exc.headers
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(str(exc.detail), codes.get(exc.status_code, "http_error")),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        errors = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
            errors.append({"field": loc, "message": err.get("msg", "invalid value")})
        return JSONResponse(
            status_code=422, content=_body("Validation failed", "validation_error", {"errors": errors})
        )

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_: Request, exc: IntegrityError):
        logger.warning("Integrity error: %s", exc.orig)
        return JSONResponse(
            status_code=409,
            content=_body("The request conflicts with existing data (duplicate or invalid reference)", "conflict"),
        )

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_: Request, exc: SQLAlchemyError):
        logger.exception("Database error")
        return JSONResponse(status_code=500, content=_body("A database error occurred", "database_error"))

    @app.exception_handler(RedisError)
    async def _redis_error(_: Request, exc: RedisError):
        logger.error("Redis error: %s", exc)
        return JSONResponse(
            status_code=503, content=_body("Seat locking / cache service is temporarily unavailable", "redis_unavailable")
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("Unhandled error")  # stack trace goes to logs only, never to the client
        return JSONResponse(status_code=500, content=_body("Internal server error", "internal_error"))
