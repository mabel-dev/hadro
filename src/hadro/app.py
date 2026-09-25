"""The FastAPI application."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__, auth
from .api import buckets_router, objects_router
from .config import Config
from .errors import MethodNotAllowed, S3Error, drain, s3_error_handler
from .storage import StorageBackend, create_backend

_WRITE_METHODS = ["PUT", "DELETE", "PATCH"]


def create_app(config: Config | None = None, backend: StorageBackend | None = None) -> FastAPI:
    """Build the application. ``backend`` overrides the one named in ``config``."""
    config = config or Config.from_env()
    app = FastAPI(
        title="hadro", version=__version__, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.config = config
    app.state.backend = backend or create_backend(config)
    app.add_exception_handler(S3Error, s3_error_handler)

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok"}

    if config.auth_enabled:
        app.add_middleware(BaseHTTPMiddleware, dispatch=_signature_check)

    # Registered before the read routes so a PUT to /bucket/key is rejected as
    # read-only rather than with FastAPI's JSON 405.
    @app.api_route("/", methods=_WRITE_METHODS, include_in_schema=False)
    @app.api_route("/{path:path}", methods=_WRITE_METHODS, include_in_schema=False)
    async def read_only(request: Request, path: str = ""):
        await drain(request)
        raise MethodNotAllowed()

    app.include_router(buckets_router)
    app.include_router(objects_router)
    return app


async def _signature_check(request: Request, call_next) -> Response:
    if request.url.path == "/health":
        return await call_next(request)
    config: Config = request.app.state.config
    body = await request.body()
    try:
        auth.verify(
            method=request.method,
            raw_path=request.scope.get("raw_path", request.url.path.encode()).decode("latin-1"),
            query_string=request.scope.get("query_string", b"").decode("latin-1"),
            headers={k.lower(): v for k, v in request.headers.items()},
            body=body,
            access_key=config.access_key,
            secret_key=config.secret_key,
        )
    except S3Error as exc:
        return await s3_error_handler(request, exc)
    return await call_next(request)
