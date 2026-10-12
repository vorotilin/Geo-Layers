"""Сборка FastAPI-приложения."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import service
from app.config import Settings, get_settings
from app.errors import install_error_handlers
from app.logs import setup_logging
from app.middleware import RequestIdMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    # Swagger UI и схема — только в dev (common-requirements §2), в prod — 404.
    app = FastAPI(
        title="apc-layers",
        version=settings.app_version,
        docs_url="/docs" if settings.is_dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_dev else None,
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
    return app


def app_factory() -> FastAPI:
    """Точка входа для gunicorn/uvicorn."""
    return create_app()
