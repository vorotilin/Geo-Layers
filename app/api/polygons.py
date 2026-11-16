"""Импорт полигонов (районов): GeoJSON → сетка `polygons` + слой (brief §2, design §4).

Синхронно (полигонов — сотни/тысячи, не миллионы). Каждый объект — Polygon/MultiPolygon
с ключом и числом. Результат — обычный draft-слой на сетке `polygons`, дальше работают
тайлы (rasterio), значение в точке (shapely), агрегаты и рейтинг.
"""

import json
import uuid
from typing import Annotated, Any

import numpy as np
from fastapi import APIRouter, File, Form, Request, UploadFile
from psycopg.types.json import Jsonb
from shapely.geometry import mapping, shape

from app.api.service import SettingsDep
from app.auth import WritePrincipal
from app.errors import ApiError
from app.grids import store
from app.stats import compute
from app.storage import get_storage

router = APIRouter(prefix="/v1", tags=["polygons"])


def _bad() -> ApiError:
    return ApiError(422, "invalid_polygons", "Файл не содержит валидных полигонов")


@router.post("/polygons", status_code=201)
def import_polygons(
    request: Request,
    principal: WritePrincipal,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    provider: Annotated[str, Form()],
    title: Annotated[str, Form()],
    period: Annotated[str, Form()],
    slug: Annotated[str, Form()],
    key: Annotated[str, Form()] = "zone_id",
    column: Annotated[str, Form()] = "val",
    metric_title: Annotated[str, Form()] = "",
    unit: Annotated[str | None, Form()] = None,
    value_kind: Annotated[str, Form()] = "intensive",
) -> dict[str, Any]:
    try:
        doc = json.loads(file.file.read())
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise _bad() from None
    features = doc.get("features") if isinstance(doc, dict) else doc
    if not isinstance(features, list) or not features:
        raise _bad()

    polygons = []
    keys = []
    values = []
    for f in features:
        if not isinstance(f, dict):
            continue
        g = f.get("geometry")
        if not g or g.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        props = f.get("properties") or {}
        k = props.get(key)
        v = props.get(column)
        if k is None or v is None:
            continue
        polygons.append(shape(g))
        keys.append(str(k))
        values.append(float(v))
    if not polygons:
        raise _bad()

    storage = get_storage(settings)
    grid_id = str(uuid.uuid4())
    snapshot_key = store.save_polygons(storage, grid_id, polygons)
    n = len(polygons)
    values32 = np.asarray(values, dtype=np.float32)
    stats = compute(values32, n)

    with request.app.state.pool.connection() as conn:
        conn.execute(
            "INSERT INTO dataset (namespace_id, provider, title) VALUES (%s, %s, %s) "
            "ON CONFLICT (namespace_id, provider, title) DO NOTHING",
            (principal.namespace_id, provider, title),
        )
        ds_row = conn.execute(
            "SELECT id FROM dataset WHERE namespace_id=%s AND provider=%s AND title=%s",
            (principal.namespace_id, provider, title),
        ).fetchone()
        assert ds_row is not None  # noqa: S101
        dataset_id = ds_row["id"]
        conn.execute(
            "INSERT INTO grid (id, namespace_id, provider, kind, crs, params, cell_count) "
            "VALUES (%s, %s, %s, 'polygons', 'OGC:CRS84', %s, %s)",
            (grid_id, principal.namespace_id, provider, Jsonb({"polygons_key": snapshot_key}), n),
        )
        for i, (k, p) in enumerate(zip(keys, polygons, strict=True)):
            gj = json.dumps(mapping(p))
            conn.execute(
                "INSERT INTO grid_cell (grid_id, idx, key, geom, area_m2) "
                "VALUES (%s, %s, %s, ST_GeomFromGeoJSON(%s), "
                "ST_Area(ST_GeomFromGeoJSON(%s)::geography))",
                (grid_id, i, k, gj, gj),
            )
        conn.execute(
            "INSERT INTO layer (dataset_id, slug, title, unit, value_kind) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (dataset_id, slug) DO NOTHING",
            (dataset_id, slug, metric_title or slug, unit, value_kind),
        )
        layer_row = conn.execute(
            "SELECT id FROM layer WHERE dataset_id=%s AND slug=%s", (dataset_id, slug)
        ).fetchone()
        assert layer_row is not None  # noqa: S101
        layer_id = layer_row["id"]
        version_id = str(uuid.uuid4())
        values_key = store.save_values(storage, version_id, values32)
        conn.execute(
            "INSERT INTO layer_version "
            "(id, layer_id, period, revision, grid_id, values_key, stats) "
            "SELECT %s, %s, %s, coalesce(max(revision), 0) + 1, %s, %s, %s "
            "FROM layer_version WHERE layer_id=%s AND period=%s",
            (version_id, layer_id, period, grid_id, values_key, Jsonb(stats), layer_id, period),
        )
    return {
        "layer_id": str(layer_id),
        "version_id": version_id,
        "grid_id": grid_id,
        "cells": n,
        "status": "draft",
    }
