"""Построение слоя из набора точек (brief §3, design §6).

Методы: `bin` (квадратная сетка), `kde` (тепловая карта), `choropleth` (картограмма по
полигонам). Для bin/kde создаётся новая rectilinear-сетка (UTM); для choropleth
переиспользуется сетка полигонов (`grid_id`). Результат — обычный draft-слой.
"""

import uuid
from typing import Any

import numpy as np
from fastapi import APIRouter, Request
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.api.imports import parse_uuid
from app.api.service import SettingsDep
from app.auth import WritePrincipal
from app.derive.binning import BinningError, bin_squares
from app.derive.choropleth import count_in_polygons, normalize, polygon_areas_m2
from app.derive.h3bin import bin_h3
from app.derive.kde import kde
from app.errors import ApiError
from app.grids import store
from app.grids.polygons import create_polygon_grid
from app.importer.points import load_snapshot
from app.stats import compute
from app.storage import LocalStorage, get_storage

router = APIRouter(prefix="/v1", tags=["build"])

DERIVED_PROVIDER = "derived"
DERIVED_DATASET = "Слои из точек"
DERIVED_PERIOD = "derived"
SLUG = r"^[a-z0-9][a-z0-9_]{0,62}$"


class BuildRequest(BaseModel):
    point_set_id: str
    version_id: str | None = None
    method: str = "bin"  # bin | kde | choropleth
    slug: str = Field(pattern=SLUG)
    title: str = Field(min_length=1, max_length=200)
    unit: str | None = None
    grid_id: str | None = None  # для choropleth: сетка полигонов
    params: dict[str, Any] = {}


def _point_set_not_found() -> ApiError:
    return ApiError(404, "point_set_not_found", "Набор точек не найден")


def _positive(params: dict[str, Any], key: str) -> float:
    value = params.get(key)
    if not isinstance(value, (int, float)) or value <= 0:
        raise ApiError(422, "build_invalid", f"Нужен положительный параметр {key}")
    return float(value)


def _filter_category(snap: dict[str, Any], category: str | None) -> dict[str, Any]:
    """Оставляет только точки нужной категории (пусто/None — все)."""
    if not category:
        return snap
    mask = snap["category"] == category
    return {
        "lon": snap["lon"][mask],
        "lat": snap["lat"][mask],
        "weight": snap["weight"][mask],
        "category": snap["category"][mask],
    }


def _resolve_snapshot(
    request: Request, principal: WritePrincipal, settings: SettingsDep, body: BuildRequest
) -> dict[str, Any]:
    storage = get_storage(settings)
    with request.app.state.pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        ps = cur.execute(
            "SELECT namespace_id FROM point_set WHERE id = %s", (body.point_set_id,)
        ).fetchone()
        if ps is None or ps["namespace_id"] != principal.namespace_id:
            raise _point_set_not_found()
        ver = cur.execute(
            "SELECT id, snapshot_key FROM point_set_version WHERE set_id = %s "
            "AND (%s::uuid IS NULL OR id = %s) ORDER BY version_no DESC LIMIT 1",
            (body.point_set_id, body.version_id, body.version_id),
        ).fetchone()
        if ver is None:
            raise ApiError(404, "point_set_version_not_found", "Версия набора не найдена")
        return load_snapshot(storage, ver["snapshot_key"])


def _derive(body: BuildRequest, snap: dict[str, Any]) -> tuple[dict, str]:
    if body.method == "bin":
        step = _positive(body.params, "step")
        agg = body.params.get("agg", "count")
        w = snap["weight"] if agg in ("sum", "mean") else None
        try:
            out = bin_squares(
                snap["lon"],
                snap["lat"],
                step=step,
                agg=agg,
                weight=w if agg == "sum" else None,
                value=w if agg == "mean" else None,
            )
        except BinningError as exc:
            raise ApiError(422, "binning_failed", str(exc)) from None
        return out, "intensive" if agg == "mean" else "extensive"
    if body.method == "kde":
        bandwidth_m = _positive(body.params, "bandwidth_m")
        kde_step = body.params.get("step")
        try:
            out = kde(
                snap["lon"],
                snap["lat"],
                bandwidth_m=bandwidth_m,
                step=float(kde_step) if kde_step else None,
                weight=snap["weight"],
            )
        except BinningError as exc:
            raise ApiError(422, "kde_failed", str(exc)) from None
        return out, "intensive"
    raise ApiError(
        422, "unsupported_method", "Метод пока не поддерживается", {"method": body.method}
    )


def _insert_layer_version(
    conn,
    storage: LocalStorage,
    principal: WritePrincipal,
    grid_id: str,
    body: BuildRequest,
    value_kind: str,
    values: np.ndarray,
    stats: dict[str, Any],
) -> tuple[str, str]:
    conn.execute(
        "INSERT INTO dataset (namespace_id, provider, title) VALUES (%s, %s, %s) "
        "ON CONFLICT (namespace_id, provider, title) DO NOTHING",
        (principal.namespace_id, DERIVED_PROVIDER, DERIVED_DATASET),
    )
    ds_row = conn.execute(
        "SELECT id FROM dataset WHERE namespace_id = %s AND provider = %s AND title = %s",
        (principal.namespace_id, DERIVED_PROVIDER, DERIVED_DATASET),
    ).fetchone()
    assert ds_row is not None  # noqa: S101 — dataset только что создан/найден
    dataset_id = ds_row["id"]
    conn.execute(
        "INSERT INTO layer (dataset_id, slug, title, unit, value_kind) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (dataset_id, slug) DO NOTHING",
        (dataset_id, body.slug, body.title, body.unit, value_kind),
    )
    layer_row = conn.execute(
        "SELECT id FROM layer WHERE dataset_id = %s AND slug = %s", (dataset_id, body.slug)
    ).fetchone()
    assert layer_row is not None  # noqa: S101 — слой только что создан/найден
    layer_id = layer_row["id"]
    version_id = str(uuid.uuid4())
    values_key = store.save_values(storage, version_id, values)
    conn.execute(
        "INSERT INTO layer_version "
        "(id, layer_id, period, revision, grid_id, values_key, stats) "
        "SELECT %s, %s, %s, coalesce(max(revision), 0) + 1, %s, %s, %s "
        "FROM layer_version WHERE layer_id = %s AND period = %s",
        (
            version_id,
            layer_id,
            DERIVED_PERIOD,
            grid_id,
            values_key,
            Jsonb(stats),
            layer_id,
            DERIVED_PERIOD,
        ),
    )
    return layer_id, version_id


def _build_choropleth(
    body: BuildRequest,
    snap: dict[str, Any],
    principal: WritePrincipal,
    settings: SettingsDep,
    storage: LocalStorage,
    request: Request,
) -> dict[str, Any]:
    grid_id = parse_uuid(
        body.grid_id or "", ApiError(404, "grid_not_found", "Сетка полигонов не найдена")
    )
    with request.app.state.pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        grid = cur.execute(
            "SELECT params, cell_count, namespace_id FROM grid WHERE id = %s AND kind = 'polygons'",
            (grid_id,),
        ).fetchone()
    if grid is None or grid["namespace_id"] != principal.namespace_id:
        raise ApiError(404, "grid_not_found", "Сетка полигонов не найдена")

    polygons = store.load_polygons(storage, grid["params"]["polygons_key"])
    agg = body.params.get("agg", "count")
    w = snap["weight"] if agg == "sum" else None
    norm = body.params.get("normalize", "none")
    try:
        counts = count_in_polygons(snap["lon"], snap["lat"], polygons, weight=w)
    except BinningError as exc:
        raise ApiError(422, "choropleth_failed", str(exc)) from None

    if norm == "per_layer":
        other_id = parse_uuid(
            body.params.get("layer_id", ""),
            ApiError(422, "choropleth_failed", "Для per_layer нужен layer_id"),
        )
        mult = float(body.params.get("multiplier", 1.0))
        with request.app.state.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            ov = cur.execute(
                "SELECT values_key FROM layer_version "
                "WHERE layer_id = %s AND grid_id = %s AND status = 'published' "
                "ORDER BY published_at DESC LIMIT 1",
                (other_id, grid_id),
            ).fetchone()
        if ov is None:
            raise ApiError(404, "layer_not_found", "Базовый слой не найден или не опубликован")
        other_values = store.load_values(storage, ov["values_key"])
        safe = np.where(other_values != 0, other_values, np.nan)
        values = counts / safe * mult
    else:
        areas = polygon_areas_m2(polygons)
        values = normalize(counts, areas, norm)

    value_kind = "extensive" if norm == "none" else "intensive"
    values32 = values.astype(np.float32)
    stats = compute(values32, len(polygons))
    with request.app.state.pool.connection() as conn:
        layer_id, version_id = _insert_layer_version(
            conn, storage, principal, grid_id, body, value_kind, values32, stats
        )
    return {
        "layer_id": str(layer_id),
        "version_id": version_id,
        "grid_id": grid_id,
        "cells": len(polygons),
        "crs": "OGC:CRS84",
        "value_kind": value_kind,
        "status": "draft",
    }


@router.post("/layers/build", status_code=202)
def build_layer(
    body: BuildRequest,
    request: Request,
    principal: WritePrincipal,
    settings: SettingsDep,
) -> dict[str, Any]:
    snap = _resolve_snapshot(request, principal, settings, body)
    snap = _filter_category(snap, body.params.get("category"))
    storage = get_storage(settings)

    if body.method == "choropleth":
        return _build_choropleth(body, snap, principal, settings, storage, request)

    if body.method == "h3":
        resolution = body.params.get("resolution")
        if not isinstance(resolution, int) or not 0 <= resolution <= 15:
            raise ApiError(422, "build_invalid", "Нужен resolution 0..15")
        agg = body.params.get("agg", "count")
        w = snap["weight"] if agg in ("sum", "mean") else None
        try:
            out = bin_h3(
                snap["lon"],
                snap["lat"],
                resolution=resolution,
                agg=agg,
                weight=w if agg == "sum" else None,
                value=snap["weight"] if agg == "mean" else None,
            )
        except BinningError as exc:
            raise ApiError(422, "h3_failed", str(exc)) from None
        value_kind = "intensive" if agg == "mean" else "extensive"
        with request.app.state.pool.connection() as conn:
            grid_id = create_polygon_grid(
                conn,
                storage,
                principal.namespace_id,
                DERIVED_PROVIDER,
                out["polygons"],
                out["keys"],
            )
            layer_id, version_id = _insert_layer_version(
                conn, storage, principal, grid_id, body, value_kind, out["values"], out["stats"]
            )
        return {
            "layer_id": str(layer_id),
            "version_id": version_id,
            "grid_id": grid_id,
            "cells": out["cells"],
            "crs": "OGC:CRS84",
            "value_kind": value_kind,
            "status": "draft",
        }

    out, value_kind = _derive(body, snap)
    grid_id = str(uuid.uuid4())
    layout_key = store.save_layout(storage, grid_id, out["cells"], out["layout"])

    with request.app.state.pool.connection() as conn:
        conn.execute(
            "INSERT INTO grid (id, namespace_id, provider, kind, crs, params, cell_count) "
            "VALUES (%s, %s, %s, 'rectilinear', %s, %s, %s)",
            (
                grid_id,
                principal.namespace_id,
                DERIVED_PROVIDER,
                out["crs"],
                Jsonb(
                    {
                        "layout_key": layout_key,
                        "rows": out["layout"].shape[0],
                        "cols": out["layout"].shape[1],
                    }
                ),
                out["cells"],
            ),
        )
        layer_id, version_id = _insert_layer_version(
            conn, storage, principal, grid_id, body, value_kind, out["values"], out["stats"]
        )
    return {
        "layer_id": str(layer_id),
        "version_id": version_id,
        "grid_id": grid_id,
        "cells": out["cells"],
        "crs": out["crs"],
        "value_kind": value_kind,
        "status": "draft",
    }
