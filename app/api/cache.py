"""Кэш тайлов: прогрев и сброс (brief §11)."""

import shutil
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.api.jobs import accepted_body
from app.api.layers import find_version
from app.api.service import SettingsDep
from app.auth import AdminPrincipal
from app.errors import ApiError
from app.jobs import queue

router = APIRouter(prefix="/v1/cache", tags=["cache"])


@router.post("/purge")
def purge_cache(
    request: Request, principal: AdminPrincipal, settings: SettingsDep
) -> dict[str, Any]:
    """Сброс дискового кэша PNG и in-memory кэшей (тайлов индексов, обзоров)."""
    from app.grids import store
    from app.tiles import render

    shutil.rmtree(settings.cache_dir, ignore_errors=True)
    Path(settings.cache_dir).mkdir(parents=True, exist_ok=True)
    for lru in (store._layouts, store._values, render._index_cache, render._overview_cache):
        lru.clear()
    return {"purged": True}


class WarmRequest(BaseModel):
    layer_id: str
    version_id: str | None = None
    tms: Literal["3857", "3395"] = "3857"
    bbox: list[float] = Field(min_length=4, max_length=4)
    min_zoom: int = Field(ge=0, le=22)
    max_zoom: int = Field(ge=0, le=22)
    style: str = "default"


@router.post("/warm", status_code=202)
def warm_cache(
    body: WarmRequest, request: Request, principal: AdminPrincipal, settings: SettingsDep
) -> dict[str, Any]:
    """Прогрев кэша тайлов по bbox и диапазону зумов (202, задание)."""
    with request.app.state.pool.connection() as conn:
        v = find_version(conn, principal.namespace_id, body.layer_id, body.version_id)
        if v is None:
            raise ApiError(404, "version_not_found", "Версия слоя не найдена")
        job = queue.enqueue(
            conn,
            "warm_cache",
            {
                "layer_id": body.layer_id,
                "version_id": str(v["id"]),
                "tms": body.tms,
                "bbox": body.bbox,
                "min_zoom": body.min_zoom,
                "max_zoom": body.max_zoom,
                "style": body.style,
            },
            namespace_id=principal.namespace_id,
            token_id=principal.token_id,
        )
    return accepted_body(settings, job)
