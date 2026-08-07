"""Exception-handling middleware and FastAPI exception handlers."""

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from app.core.exceptions import AppError


async def request_context_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Attach a request id, time the request, and log unhandled exceptions."""
    request_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    with logger.contextualize(request_id=request_id):
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled exception while processing request")
            raise
        else:
            duration_ms = (time.perf_counter() - start_time) * 1000
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "{method} {path} -> {status} ({duration_ms:.2f}ms)",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=duration_ms,
            )
            return response


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers on the FastAPI app."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning("AppError on {path}: {message}", path=request.url.path, message=exc.message)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unexpected error on {path}", path=request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
