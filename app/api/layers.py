"""Слои: версии и публикация, значение в точке, подписанные тайлы (design §8, план M1 шаг 8)."""

import base64
import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
import psycopg
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from app.api.imports import parse_uuid
from app.api.service import SettingsDep
from app.api.styles import DEFAULT_STYLE, resolve_style
from app.auth import Principal, ReadPrincipal, WritePrincipal, find_principal
from app.config import Settings
from app.errors import ApiError
from app.grids import store
from app.grids.crs import WGS84, normalize_crs, transformer
from app.jobs import queue
from app.ratelimit import check_rate_limit_key
from app.signing import rounded_exp, sign, verify
from app.storage import LocalStorage, get_storage
from app.styles import legend as build_legend
from app.tiles import render
from app.tiles.overview import overview_min_zoom

router = APIRouter(prefix="/v1", tags=["layers"])

MAX_VALUE_LAYERS = 20
MAX_PAGE = 200
TILE_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}

VERSION_SELECT = """
    SELECT v.id, v.layer_id, v.period, v.revision, v.status, v.values_key, v.stats,
           v.created_at, v.published_at, v.grid_id,
           l.slug, l.title, l.unit, l.value_kind, d.namespace_id,
           g.crs, g.kind, g.params AS grid_params, g.cell_count
    FROM layer_version v
    JOIN layer l ON l.id = v.layer_id
    JOIN dataset d ON d.id = l.dataset_id
    JOIN grid g ON g.id = v.grid_id
"""


def _layer_not_found() -> ApiError:
    return ApiError(404, "layer_not_found", "Слой не найден")


def _version_not_found() -> ApiError:
    return ApiError(404, "version_not_found", "Версия слоя не найдена")


def find_version(
    conn: psycopg.Connection[Any], namespace: str, layer_id: str, version_id: str | None
) -> dict[str, Any] | None:
    """Конкретная версия слоя или текущая: последняя опубликованная последнего периода."""
    cur = conn.cursor(row_factory=dict_row)
    if version_id:
        return cur.execute(
            VERSION_SELECT + " WHERE v.id = %s AND v.layer_id = %s AND d.namespace_id = %s",
            (version_id, layer_id, namespace),
        ).fetchone()
    return cur.execute(
        VERSION_SELECT
        + """ WHERE v.layer_id = %s AND d.namespace_id = %s AND v.status = 'published'
              ORDER BY v.period DESC, v.published_at DESC LIMIT 1""",
        (layer_id, namespace),
    ).fetchone()


def public_version(v: dict[str, Any], full: bool = False) -> dict[str, Any]:
    stats = v["stats"] or {}
    out = {
        "id": str(v["id"]),
        "layer_id": str(v["layer_id"]),
        "period": v["period"],
        "revision": v["revision"],
        "status": v["status"],
        "grid_id": str(v["grid_id"]),
        "created_at": v["created_at"],
        "published_at": v["published_at"],
        "stats": stats
        if full
        else {k: stats.get(k) for k in ("count", "coverage", "min", "max", "mean")},
    }
    return out


def _encode_cursor(created_at: Any, row_id: Any) -> str:
    raw = f"{created_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        ts, _, row_id = raw.partition("|")
        return datetime.fromisoformat(ts), str(uuid.UUID(row_id))
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "bad_cursor", "Некорректный курсор") from None


@router.get("/layers")
def list_layers(
    request: Request,
    principal: ReadPrincipal,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Каталог слоёв namespace по времени создания; страницы — курсором."""
    after = _decode_cursor(cursor) if cursor else None
    with request.app.state.pool.connection() as conn:
        rows = (
            conn.cursor(row_factory=dict_row)
            .execute(
                """
            SELECT l.id, l.slug, l.title, l.unit, l.value_kind, l.created_at,
                   d.id AS dataset_id, d.provider, d.title AS dataset_title,
                   (SELECT v.id FROM layer_version v
                    WHERE v.layer_id = l.id AND v.status = 'published'
                    ORDER BY v.period DESC, v.published_at DESC LIMIT 1) AS current_version_id,
                   (SELECT count(*) FROM layer_version v WHERE v.layer_id = l.id) AS versions
            FROM layer l JOIN dataset d ON d.id = l.dataset_id
            WHERE d.namespace_id = %s
              AND (%s::timestamptz IS NULL OR (l.created_at, l.id) > (%s::timestamptz, %s::uuid))
            ORDER BY l.created_at, l.id
            LIMIT %s
            """,
                (
                    principal.namespace_id,
                    after and after[0],
                    after and after[0],
                    after and after[1],
                    limit + 1,
                ),
            )
            .fetchall()
        )
    page = rows[:limit]
    return {
        "items": [
            {
                "id": str(r["id"]),
                "slug": r["slug"],
                "title": r["title"],
                "unit": r["unit"],
                "value_kind": r["value_kind"],
                "dataset": {
                    "id": str(r["dataset_id"]),
                    "provider": r["provider"],
                    "title": r["dataset_title"],
                },
                "current_version_id": str(r["current_version_id"])
                if r["current_version_id"]
                else None,
                "versions": r["versions"],
                "created_at": r["created_at"],
            }
            for r in page
        ],
        "next_cursor": _encode_cursor(page[-1]["created_at"], page[-1]["id"])
        if len(rows) > limit
        else None,
    }


@router.get("/layers/{layer_id}")
def get_layer(layer_id: str, request: Request, principal: ReadPrincipal) -> dict[str, Any]:
    layer_id = parse_uuid(layer_id, _layer_not_found())
    with request.app.state.pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        layer = cur.execute(
            """
            SELECT l.id, l.slug, l.title, l.unit, l.value_kind, l.created_at,
                   d.id AS dataset_id, d.provider, d.title AS dataset_title
            FROM layer l JOIN dataset d ON d.id = l.dataset_id
            WHERE l.id = %s AND d.namespace_id = %s
            """,
            (layer_id, principal.namespace_id),
        ).fetchone()
        if layer is None:
            raise _layer_not_found()
        versions = cur.execute(
            VERSION_SELECT + " WHERE v.layer_id = %s ORDER BY v.period DESC, v.revision DESC",
            (layer_id,),
        ).fetchall()
        current = find_version(conn, principal.namespace_id, layer_id, None)
    return {
        "id": str(layer["id"]),
        "slug": layer["slug"],
        "title": layer["title"],
        "unit": layer["unit"],
        "value_kind": layer["value_kind"],
        "dataset": {
            "id": str(layer["dataset_id"]),
            "provider": layer["provider"],
            "title": layer["dataset_title"],
        },
        "current_version_id": str(current["id"]) if current else None,
        "versions": [public_version(v) for v in versions],
        "created_at": layer["created_at"],
    }


@router.get("/layers/{layer_id}/versions/{version_id}")
def get_version(
    layer_id: str, version_id: str, request: Request, principal: ReadPrincipal
) -> dict[str, Any]:
    with request.app.state.pool.connection() as conn:
        v = find_version(
            conn,
            principal.namespace_id,
            parse_uuid(layer_id, _layer_not_found()),
            parse_uuid(version_id, _version_not_found()),
        )
    if v is None:
        raise _version_not_found()
    return public_version(v, full=True)


@router.get("/layers/{layer_id}/legend")
def get_legend(
    layer_id: str,
    request: Request,
    principal: ReadPrincipal,
    version: str | None = None,
    style: str = DEFAULT_STYLE,
) -> dict[str, Any]:
    """Легенда слоя: те же breaks + палитра, что и тайлы (не расходятся по построению)."""
    layer_id_u = parse_uuid(layer_id, _layer_not_found())
    version_u = parse_uuid(version, _version_not_found()) if version else None
    with request.app.state.pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        layer_exists = cur.execute(
            "SELECT 1 FROM layer l JOIN dataset d ON d.id = l.dataset_id "
            "WHERE l.id = %s AND d.namespace_id = %s",
            (layer_id_u, principal.namespace_id),
        ).fetchone()
        if layer_exists is None:
            raise _layer_not_found()
        v = find_version(conn, principal.namespace_id, layer_id_u, version_u)
        if v is None:
            if version_u:
                raise _version_not_found()
            raise ApiError(409, "no_published_version", "У слоя нет опубликованной версии")
        style_obj, _ = resolve_style(conn, v["namespace_id"], style, latest=True)
    if style_obj is None:
        raise ApiError(404, "style_not_found", "Стиль не найден")
    return build_legend(style_obj, v["stats"], v["unit"])


@router.post("/layers/{layer_id}/versions/{version_id}/publish")
def publish_version(
    layer_id: str,
    version_id: str,
    request: Request,
    principal: WritePrincipal,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Публикация: версия становится текущей для своего периода. Повтор — без изменений.

    Для WGS84-прямоугольных сеток дополнительно ставится прогрев обзорной пирамиды
    (0..z_s−1) в обеих тайловых схемах (design §5.3).
    """
    layer_id = parse_uuid(layer_id, _layer_not_found())
    version_id = parse_uuid(version_id, _version_not_found())
    with request.app.state.pool.connection() as conn:
        v = find_version(conn, principal.namespace_id, layer_id, version_id)
        if v is None:
            raise _version_not_found()
        if v["status"] != "published":
            conn.execute(
                "UPDATE layer_version SET status = 'published', published_at = now() WHERE id = %s",
                (version_id,),
            )
            v = find_version(conn, principal.namespace_id, layer_id, version_id)
            assert v is not None  # noqa: S101 — только что опубликована
            _enqueue_overview_warm(conn, request, settings, principal, v)
    assert v is not None  # noqa: S101
    return public_version(v)


def _enqueue_overview_warm(
    conn: Any, request: Request, settings: Settings, principal: Principal, v: dict[str, Any]
) -> None:
    """Прогрев обзорных зумов (0..z_s−1) в 3857 и 3395 для WGS84-прямоугольной сетки."""
    if v["kind"] != "rectilinear" or normalize_crs(v["crs"]).name != WGS84:
        return
    storage = get_storage(settings)
    layout = store.load_layout(storage, v["grid_params"]["layout_key"])
    z_s = overview_min_zoom(layout)
    if z_s <= 0:
        return
    bbox = [
        float(layout.x_edges[0]),
        float(layout.y_edges[0]),
        float(layout.x_edges[-1]),
        float(layout.y_edges[-1]),
    ]
    for tms in ("3857", "3395"):
        queue.enqueue(
            conn,
            "warm_cache",
            {
                "layer_id": str(v["layer_id"]),
                "version_id": str(v["id"]),
                "tms": tms,
                "bbox": bbox,
                "min_zoom": 0,
                "max_zoom": max(z_s - 1, 0),
                "style": "default",
            },
            namespace_id=principal.namespace_id,
            token_id=principal.token_id,
        )


# --- значение в точке ---


def _layer_refs(raw: str) -> list[tuple[str, str | None]]:
    refs = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        layer, _, version = part.partition("@")
        refs.append(
            (
                parse_uuid(layer, _layer_not_found()),
                parse_uuid(version, _version_not_found()) if version else None,
            )
        )
    if not refs:
        raise ApiError(422, "layers_required", "Нужен хотя бы один слой")
    if len(refs) > MAX_VALUE_LAYERS:
        raise ApiError(422, "too_many_layers", "Слишком много слоёв", {"max": MAX_VALUE_LAYERS})
    return refs


def value_at(storage: LocalStorage, v: dict[str, Any], lng: float, lat: float) -> float | None:
    values = store.load_values(storage, v["values_key"])
    if v["kind"] == "polygons":
        from app.grids.polygons import PolygonGrid

        grid = PolygonGrid(store.load_polygons(storage, v["grid_params"]["polygons_key"]))
        idx = grid.lookup(np.array([lng]), np.array([lat]))
    else:
        crs = normalize_crs(v["crs"])
        layout = store.load_layout(storage, v["grid_params"]["layout_key"])
        x, y = (lng, lat) if crs.is_wgs84 else transformer(WGS84, crs.name).transform(lng, lat)
        idx = layout.lookup(np.array([x]), np.array([y]))
    value = store.take(values, idx)[0]
    return float(value) if np.isfinite(value) else None


@router.get("/values")
def get_values(
    request: Request,
    principal: ReadPrincipal,
    settings: SettingsDep,
    lat: Annotated[float, Query(ge=-90, le=90)],
    lng: Annotated[float, Query(ge=-180, le=180)],
    layers: Annotated[
        str, Query(description="id слоёв через запятую, `id@version` — конкретная версия")
    ],
) -> dict[str, Any]:
    """Значение в точке по слоям. null — ячейки нет или значение неизвестно."""
    storage = get_storage(settings)
    out: list[dict[str, Any]] = []
    with request.app.state.pool.connection() as conn:
        for layer_id, version_id in _layer_refs(layers):
            v = find_version(conn, principal.namespace_id, layer_id, version_id)
            if v is None:
                out.append(
                    {
                        "layer_id": layer_id,
                        "version_id": None,
                        "value": None,
                        "reason": "version_not_found" if version_id else "no_published_version",
                    }
                )
                continue
            out.append(
                {
                    "layer_id": layer_id,
                    "version_id": str(v["id"]),
                    "period": v["period"],
                    "value": value_at(storage, v, lng, lat),
                    "unit": v["unit"],
                }
            )
    return {"lat": lat, "lng": lng, "values": out}


# --- тайлы ---


def tile_prefix(layer_id: str, version_id: str, style: str, tms: str) -> str:
    return f"/v1/tiles/{layer_id}/{version_id}/{style}/{tms}/"


def _secrets(settings: Settings) -> list[str]:
    return [s for s in (settings.url_signing_secret, settings.url_signing_secret_previous) if s]


class SignRequest(BaseModel):
    layer: str
    version: str | None = None
    style: str = DEFAULT_STYLE
    tms: Literal["3857", "3395"] = "3857"
    ttl: int | None = Field(default=None, ge=60, le=30 * 86400)


@router.post("/tiles/sign")
def sign_tiles(
    body: SignRequest, request: Request, principal: ReadPrincipal, settings: SettingsDep
) -> dict[str, Any]:
    """Подписанный шаблон URL тайлов слоя. Без version — текущая опубликованная версия."""
    if not settings.url_signing_secret:
        raise ApiError(503, "signing_not_configured", "Не задан URL_SIGNING_SECRET")
    layer_id = parse_uuid(body.layer, _layer_not_found())
    version_id = parse_uuid(body.version, _version_not_found()) if body.version else None
    with request.app.state.pool.connection() as conn:
        v = find_version(conn, principal.namespace_id, layer_id, version_id)
        if v is None:
            if version_id:
                raise _version_not_found()
            raise ApiError(409, "no_published_version", "У слоя нет опубликованной версии")
        _, style_ref = resolve_style(conn, v["namespace_id"], body.style, latest=True)
    if style_ref is None:
        raise ApiError(404, "style_not_found", "Стиль не найден")
    exp = rounded_exp(
        body.ttl or settings.signed_url_ttl_seconds, settings.sign_exp_rounding_seconds
    )
    prefix = tile_prefix(layer_id, str(v["id"]), style_ref, body.tms)
    sig = sign(prefix, exp, settings.url_signing_secret)
    base = settings.public_base_url.rstrip("/")
    return {
        "url_template": f"{base}{prefix}{{z}}/{{x}}/{{y}}.png?exp={exp}&sig={sig}",
        "layer_id": layer_id,
        "version_id": str(v["id"]),
        "style": style_ref,
        "tms": body.tms,
        "exp": exp,
    }


def _authorize_tile(request: Request, settings: Settings, prefix: str) -> Principal | None:
    """Подпись — основной путь. Bearer-токен read — для отладки и серверных клиентов."""
    q = request.query_params
    if verify(prefix, q.get("exp"), q.get("sig"), _secrets(settings)):
        return None
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        with request.app.state.pool.connection() as conn:
            principal = find_principal(conn, token.strip())
        if principal and principal.can("read"):
            return principal
    raise ApiError(403, "bad_signature", "Подпись ссылки недействительна или истекла")


def _parse_y(raw: str) -> tuple[int, int]:
    name = raw.removesuffix(".png")
    if name == raw:
        raise ApiError(404, "not_found", "Объект не найден")
    size = 256
    if name.endswith("@2x"):
        name, size = name[:-3], 512
    if not name.isdigit():
        raise ApiError(404, "not_found", "Объект не найден")
    return int(name), size


@router.get("/tiles/{layer_id}/{version_id}/{style}/{tms}/{z}/{x}/{y}")
def get_tile(
    layer_id: str,
    version_id: str,
    style: str,
    tms: str,
    z: int,
    x: int,
    y: str,
    request: Request,
    settings: SettingsDep,
) -> Response:
    if tms not in render.TMS:
        raise ApiError(
            404,
            "tms_not_found",
            "Тайловая схема не поддерживается",
            {"supported": list(render.TMS)},
        )
    y_num, size = _parse_y(y)
    prefix = tile_prefix(layer_id, version_id, style, tms)
    principal = _authorize_tile(request, settings, prefix)
    check_rate_limit_key(prefix, settings.rate_limit_tiles_rps)
    if not render.tile_in_range(tms, z, x, y_num):
        raise ApiError(404, "tile_out_of_range", "Тайл вне схемы")

    suffix = "@2x" if size == 512 else ""
    rel = f"tiles/{layer_id}/{version_id}/{style}/{tms}/{z}/{x}/{y_num}{suffix}.png"
    etag = '"' + hashlib.sha256(rel.encode()).hexdigest()[:20] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=TILE_CACHE_HEADERS | {"ETag": etag})
    headers = TILE_CACHE_HEADERS | {"ETag": etag}
    cache = LocalStorage(settings.cache_dir)
    path: Path = cache.path(rel)
    if path.exists():
        return FileResponse(path, media_type="image/png", headers=headers)

    layer_uuid = parse_uuid(layer_id, _layer_not_found())
    version_uuid = parse_uuid(version_id, _version_not_found())
    with request.app.state.pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        v = cur.execute(
            VERSION_SELECT + " WHERE v.id = %s AND v.layer_id = %s",
            (version_uuid, layer_uuid),
        ).fetchone()
        if v is None or (principal is not None and v["namespace_id"] != principal.namespace_id):
            raise _version_not_found()
        style_obj, style_ref = resolve_style(conn, v["namespace_id"], style)
    if style_ref is None:
        raise ApiError(404, "style_not_found", "Стиль не найден")
    storage = get_storage(settings)
    values = store.load_values(storage, v["values_key"])
    if v["kind"] == "polygons":
        polygons = store.load_polygons(storage, v["grid_params"]["polygons_key"])
        png = render.render_polygon_tile(
            values, polygons, v["stats"], tms, z, x, y_num, size, style=style_obj
        )
    else:
        png = render.render(
            values,
            store.load_layout(storage, v["grid_params"]["layout_key"]),
            v["grid_params"]["layout_key"],
            normalize_crs(v["crs"]),
            v["stats"],
            tms,
            z,
            x,
            y_num,
            size,
            style=style_obj,
            value_kind=v["value_kind"],
            values_key=v["values_key"],
        )
    cache.write_bytes(rel, png)
    return Response(png, media_type="image/png", headers=headers)
