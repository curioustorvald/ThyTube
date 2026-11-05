"""FastAPI application factory."""

from __future__ import annotations

import mimetypes
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..config import Config
from .common import ApiError
from .routes import manage, system, videos

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/vtt", ".vtt")


def _error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, **extra}}, status_code=status)


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="ThyTube", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.cfg = cfg

    @app.exception_handler(ApiError)
    async def api_error(_req: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message, **exc.extra)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_req: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []) if x not in ("body", "query"))
        return _error(422, "invalid_request", f"{loc}: {first.get('msg', 'invalid')}".strip(": "))

    @app.middleware("http")
    async def no_store(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    for r in (system.router, videos.router, manage.router):
        app.include_router(r, prefix="/api")

    # Production: Apache serves these. Development: let uvicorn do it.
    if cfg.server.serve_media:
        cfg.ensure_pool_dirs()
        app.mount("/media", StaticFiles(directory=cfg.media_dir), name="media")
        app.mount("/avatars", StaticFiles(directory=cfg.channels_dir), name="avatars")
    if cfg.server.serve_static and cfg.web_dir.is_dir():
        app.mount("/", StaticFiles(directory=cfg.web_dir, html=True), name="web")
    return app
