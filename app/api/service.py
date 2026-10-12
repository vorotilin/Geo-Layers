"""Служебные ручки: /health, /ready (common-requirements §4)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.readiness import run_checks

router = APIRouter(tags=["service"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/health")
def health(settings: SettingsDep) -> dict[str, str]:
    """Процесс жив. Зависимости не проверяет."""
    return {"status": "ok", "version": settings.app_version}


@router.get("/ready")
def ready(settings: SettingsDep) -> JSONResponse:
    """Готов принимать запросы: все зависимости доступны."""
    checks = run_checks(settings)
    ok = all(v == "ok" for v in checks.values())
    if ok:
        return JSONResponse({"status": "ready", "checks": checks})
    return JSONResponse(
        status_code=503,
        content={
            "error": "not_ready",
            "message": "Зависимости сервиса недоступны",
            "details": {"checks": checks},
        },
        headers={"Retry-After": "5"},
    )
