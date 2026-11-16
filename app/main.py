"""Сборка FastAPI-приложения."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    build,
    cache,
    data,
    imports,
    jobs,
    layers,
    point_sets,
    polygons,
    service,
    styles,
    tokens,
)
from app.config import Settings, get_settings
from app.db import create_pool
from app.errors import install_error_handlers
from app.logs import setup_logging
from app.middleware import RequestIdMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.pool = create_pool(settings)
        try:
            yield
        finally:
            app.state.pool.close()

    # Swagger UI и схема — только в dev (common-requirements §2), в prod — 404.
    app = FastAPI(
        title="apc-layers",
        version=settings.app_version,
        docs_url="/docs" if settings.is_dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_dev else None,
        lifespan=lifespan,
    )
    app.dependency_overrides[get_settings] = lambda: settings

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-Id"],
        )
    app.add_middleware(RequestIdMiddleware)

    install_error_handlers(app)
    app.include_router(service.router)
    app.include_router(jobs.router)
    app.include_router(imports.router)
    app.include_router(layers.router)
    app.include_router(styles.router)
    app.include_router(tokens.router)
    app.include_router(point_sets.router)
    app.include_router(build.router)
    app.include_router(data.router)
    app.include_router(cache.router)
    app.include_router(polygons.router)

    # Веб-интерфейс (ADR-0010): статика из сборки Vite, ходит только в публичный /v1.
    if Path(settings.ui_dir, "index.html").is_file():

        @app.get("/ui", include_in_schema=False)
        def ui_root() -> RedirectResponse:
            return RedirectResponse("/ui/")

        app.mount("/ui", StaticFiles(directory=settings.ui_dir, html=True), name="ui")
    return app


def app_factory() -> FastAPI:
    """Точка входа для gunicorn/uvicorn."""
    return create_app()
