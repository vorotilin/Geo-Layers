"""Данные слоёв: пакет точек, агрегат по bbox/полигону, рейтинг (brief §7).

Агрегат и рейтинг работают на обычной сетке слоя (rectilinear) — одинаково для слоёв
поставщиков и слоёв из точек. Ячейка относится к региону, если её центр внутри него.
"""

from typing import Annotated, Any, Literal

import numpy as np
import numpy.typing as npt
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from shapely.geometry import shape
from shapely.ops import transform as sh_transform

from app.api.imports import parse_uuid
from app.api.layers import find_version
from app.api.service import SettingsDep
from app.auth import ReadPrincipal
from app.errors import ApiError
from app.grids import store
from app.grids.crs import WGS84, normalize_crs, transformer
from app.ratelimit import check_rate_limit
from app.storage import get_storage

router = APIRouter(prefix="/v1", tags=["data"])

MAX_BATCH_POINTS = 10_000
MAX_RATING = 10_000


def _layer_not_found() -> ApiError:
    return ApiError(404, "layer_not_found", "Слой не найден")


def _parse_refs(raw: str) -> list[tuple[str, str | None]]:
    refs: list[tuple[str, str | None]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        layer, _, version = part.partition("@")
        lid = parse_uuid(layer, _layer_not_found())
        vid = (
            parse_uuid(version, ApiError(404, "version_not_found", "Версия не найдена"))
            if version
            else None
        )
        refs.append((lid, vid))
    if not refs:
        raise ApiError(422, "layers_required", "Нужен хотя бы один слой")
    return refs


def _cell_values(
    storage, v: dict[str, Any], lngs: npt.ArrayLike, lats: npt.ArrayLike
) -> npt.NDArray[np.float32]:
    crs = normalize_crs(v["crs"])
    layout = store.load_layout(storage, v["grid_params"]["layout_key"])
    x, y = (lngs, lats) if crs.is_wgs84 else transformer(WGS84, crs.name).transform(lngs, lats)
    idx = layout.lookup(x, y)
    return store.take(store.load_values(storage, v["values_key"]), idx)


def _aggregate(
    values: npt.NDArray[np.float32], layout, crs, bbox: list[float] | None, geometry: dict | None
) -> dict[str, Any]:
    xc = (layout.x_edges[:-1] + layout.x_edges[1:]) / 2
    yc = (layout.y_edges[:-1] + layout.y_edges[1:]) / 2
    if bbox is not None:
        w, s, e, n = bbox
        if crs.is_wgs84:
            xmin, ymin, xmax, ymax = w, s, e, n
        else:
            to_grid = transformer(WGS84, crs.name)
            xmin, ymin = to_grid.transform(w, s)
            xmax, ymax = to_grid.transform(e, n)
        mask = (
            (xc[None, :] >= xmin)
            & (xc[None, :] < xmax)
            & (yc[:, None] >= ymin)
            & (yc[:, None] < ymax)
        )
    else:
        poly = shape(geometry)
        if not crs.is_wgs84:
            to_grid = transformer(WGS84, crs.name)
            poly = sh_transform(lambda x, y, z=None: to_grid.transform(x, y), poly)
        minx, miny, maxx, maxy = poly.bounds
        col_lo = int(np.searchsorted(xc, minx, "left"))
        col_hi = int(np.searchsorted(xc, maxx, "right"))
        row_lo = int(np.searchsorted(yc, miny, "left"))
        row_hi = int(np.searchsorted(yc, maxy, "right"))
        mask = np.zeros((layout.shape[0], layout.shape[1]), dtype=bool)
        if col_hi > col_lo and row_hi > row_lo:
            xx, yy = np.meshgrid(xc[col_lo:col_hi], yc[row_lo:row_hi])
            mask[row_lo:row_hi, col_lo:col_hi] = poly.contains_xy(xx.ravel(), yy.ravel()).reshape(
                xx.shape
            )
    v = store.take(values, layout.index[mask])
    finite = v[np.isfinite(v)]
    total = int(mask.sum())
    return {
        "cells": int(np.isfinite(v).sum()),
        "coverage": round(finite.size / total, 6) if total else 0.0,
        "mean": float(finite.mean()) if finite.size else None,
        "sum": float(finite.sum()) if finite.size else 0.0,
        "min": float(finite.min()) if finite.size else None,
        "max": float(finite.max()) if finite.size else None,
    }


class BatchPoint(BaseModel):
    id: str
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class BatchRequest(BaseModel):
    points: list[BatchPoint] = Field(min_length=1, max_length=MAX_BATCH_POINTS)
    layers: str


class AggregateRequest(BaseModel):
    layers: str
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    geometry: dict[str, Any] | None = None


@router.post("/values/batch")
def batch_values(
    body: BatchRequest, request: Request, principal: ReadPrincipal, settings: SettingsDep
) -> dict[str, Any]:
    """Значения по слоям для пакета точек (id, lat, lng)."""
    check_rate_limit(principal, settings.rate_limit_expensive_rps)
    storage = get_storage(settings)
    lngs = np.array([p.lng for p in body.points], dtype=np.float64)
    lats = np.array([p.lat for p in body.points], dtype=np.float64)
    result: list[dict[str, Any]] = []
    with request.app.state.pool.connection() as conn:
        for layer_ref, version_ref in _parse_refs(body.layers):
            v = find_version(conn, principal.namespace_id, layer_ref, version_ref)
            if v is None:
                result.append(
                    {
                        "layer_id": layer_ref,
                        "version_id": None,
                        "reason": "version_not_found" if version_ref else "no_published_version",
                        "items": [],
                    }
                )
                continue
            vals = _cell_values(storage, v, lngs, lats)
            result.append(
                {
                    "layer_id": layer_ref,
                    "version_id": str(v["id"]),
                    "period": v["period"],
                    "unit": v["unit"],
                    "items": [
                        {"id": p.id, "value": float(val) if np.isfinite(val) else None}
                        for p, val in zip(body.points, vals, strict=False)
                    ],
                }
            )
    return {"values": result}


@router.post("/aggregate")
def aggregate(
    body: AggregateRequest, request: Request, principal: ReadPrincipal, settings: SettingsDep
) -> dict[str, Any]:
    """Агрегат по bbox [w,s,e,n] или GeoJSON-полигону: mean/sum/min/max/cells/coverage."""
    if body.bbox is None and body.geometry is None:
        raise ApiError(422, "region_required", "Нужен bbox или geometry")
    check_rate_limit(principal, settings.rate_limit_expensive_rps)
    storage = get_storage(settings)
    result: list[dict[str, Any]] = []
    with request.app.state.pool.connection() as conn:
        for layer_ref, version_ref in _parse_refs(body.layers):
            v = find_version(conn, principal.namespace_id, layer_ref, version_ref)
            if v is None:
                result.append(
                    {
                        "layer_id": layer_ref,
                        "reason": "version_not_found" if version_ref else "no_published_version",
                    }
                )
                continue
            layout = store.load_layout(storage, v["grid_params"]["layout_key"])
            values = store.load_values(storage, v["values_key"])
            agg = _aggregate(values, layout, normalize_crs(v["crs"]), body.bbox, body.geometry)
            result.append(
                {"layer_id": layer_ref, "version_id": str(v["id"]), "period": v["period"], **agg}
            )
    return {"aggregates": result}


@router.get("/layers/{layer_id}/rating")
def rating(
    layer_id: str,
    request: Request,
    principal: ReadPrincipal,
    settings: SettingsDep,
    version: str | None = None,
    order: Literal["desc", "asc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=MAX_RATING)] = 10,
) -> dict[str, Any]:
    """Рейтинг ячеек по значению (по умолчанию — наибольшие)."""
    lid = parse_uuid(layer_id, _layer_not_found())
    vid = (
        parse_uuid(version, ApiError(404, "version_not_found", "Версия не найдена"))
        if version
        else None
    )
    storage = get_storage(settings)
    with request.app.state.pool.connection() as conn:
        v = find_version(conn, principal.namespace_id, lid, vid)
    if v is None:
        raise _layer_not_found()
    crs = normalize_crs(v["crs"])
    layout = store.load_layout(storage, v["grid_params"]["layout_key"])
    values = store.load_values(storage, v["values_key"])
    xc = (layout.x_edges[:-1] + layout.x_edges[1:]) / 2
    yc = (layout.y_edges[:-1] + layout.y_edges[1:]) / 2
    idx = layout.index
    vv = values[idx[idx >= 0]]
    finite = np.isfinite(vv)
    pos = np.flatnonzero(finite)
    pos = pos[np.argsort(vv[finite])[::-1]] if order == "desc" else pos[np.argsort(vv[finite])]
    rows, cols = np.nonzero(idx >= 0)
    lon_c, lat_c = xc[cols], yc[rows]
    if not crs.is_wgs84:
        to_ll = transformer(crs.name, WGS84)
        lon_c, lat_c = to_ll.transform(lon_c, lat_c)
    items = [
        {"rank": i + 1, "value": float(vv[p]), "lon": float(lon_c[p]), "lat": float(lat_c[p])}
        for i, p in enumerate(pos[:limit])
    ]
    return {"layer_id": layer_id, "version_id": str(v["id"]), "order": order, "items": items}
