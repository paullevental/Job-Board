"""App factory and the error envelope.

`create_app()` is a factory rather than a module-level `app` so tests can build
a fresh instance and override the session dependency per test.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from .errors import ApiError
from .routes import ROUTERS

# Status codes the frontend branches on get a stable name; anything else is
# reported honestly as a generic HTTP error rather than given a fake code.
_CODES = {404: "not_found", 409: "conflict", 422: "invalid_request"}

log = logging.getLogger("job_poller.api")


def create_app() -> FastAPI:
    app = FastAPI(
        title="job_poller",
        version=__version__,
        description="Read API over the posting history the poller collects.",
    )

    for router in ROUTERS:
        app.include_router(router)

    @app.exception_handler(ApiError)
    def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _envelope(exc.status_code, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _CODES.get(exc.status_code, "http_error")
        return _envelope(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Last line of defence. The frontend branches on `error.code`, so even a
        # bug has to arrive in the same shape -- and the message is deliberately
        # fixed text, because `str(exc)` can carry a board URL or a token.
        log.exception("unhandled error", exc_info=exc)
        return _envelope(500, "internal_error", "something went wrong on our end")

    @app.exception_handler(RequestValidationError)
    def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's error list is useful but shaped for developers; the client
        # gets one readable sentence in the same envelope as everything else.
        return _envelope(422, "invalid_request", _describe(exc))

    return app


def _envelope(status_code: int, code: str, message: str) -> JSONResponse:
    """The one error shape. No tracebacks, no internal detail."""
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def _describe(exc: RequestValidationError) -> str:
    parts = []
    for error in exc.errors():
        field = ".".join(str(loc) for loc in error.get("loc", ()) if loc != "query")
        parts.append(f"{field}: {error.get('msg', 'invalid')}" if field else error.get("msg", "invalid"))
    return "; ".join(parts) or "invalid request"
