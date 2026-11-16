"""Наборы точек: создание, пополнение версией, список (brief §3, §8)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile
from psycopg.rows import dict_row

from app.api.service import SettingsDep
from app.auth import ReadPrincipal, WritePrincipal
from app.config import Settings
from app.errors import ApiError
from app.importer.points import PointsError, load_snapshot, parse_points, save_snapshot
from app.storage import get_storage
from app.tiles import heatmap

router = APIRouter(prefix="/v1/point-sets", tags=["point-sets"])

MAX_POINTS_BYTES = 512 * 1024**2
MAX_PAGE = 200


def _not_found() -> ApiError:
    return ApiError(404, "point_set_not_found", "Набор точек не найден")


def _parse_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise _not_found() from None


def _parse(
    file: UploadFile,
    settings: Settings,
    lon: str,
    lat: str,
    weight: str | None,
    category: str | None,
) -> tuple[dict[str, Any], str]:
    raw = file.file.read()
    if len(raw) > MAX_POINTS_BYTES:
        raise ApiError(
            413, "file_too_large", "Файл больше допустимого", {"max_bytes": MAX_POINTS_BYTES}
        )
    try:
        parsed = parse_points(raw, lon=lon, lat=lat, weight=weight, category=category)
    except PointsError as exc:
        raise ApiError(422, exc.error, exc.message) from None
    key = save_snapshot(get_storage(settings), str(uuid.uuid4()), parsed)
    return parsed, key


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "created_at": row["created_at"],
        "versions": row["versions"],
        "latest_version_no": row["latest_version_no"],
    }


@router.post("", status_code=201)
def create_point_set(
    request: Request,
    principal: WritePrincipal,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()],
    lon: Annotated[str, Form()] = "lng",
    lat: Annotated[str, Form()] = "lat",
    weight: Annotated[str | None, Form()] = None,
    category: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Создаёт набор с первой версией (файл точек + маппинг колонок)."""
    set_id = str(uuid.uuid4())
    parsed, key = _parse(file, settings, lon, lat, weight, category)
    with request.app.state.pool.connection() as conn:
        conn.execute(
            "INSERT INTO point_set (id, namespace_id, title) VALUES (%s, %s, %s)",
            (set_id, principal.namespace_id, title),
        )
        conn.execute(
            "INSERT INTO point_set_version (id, set_id, version_no, count, snapshot_key) "
            "VALUES (%s, %s, 1, %s, %s)",
            (str(uuid.uuid4()), set_id, parsed["count"], key),
        )
    return {
        "id": set_id,
        "title": title,
        "version_no": 1,
        "count": parsed["count"],
        "errors": parsed["errors"],
    }


@router.post("/{set_id}/versions", status_code=201)
def add_version(
    set_id: str,
    request: Request,
    principal: WritePrincipal,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    lon: Annotated[str, Form()] = "lng",
    lat: Annotated[str, Form()] = "lat",
    weight: Annotated[str | None, Form()] = None,
    category: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Пополнение набора новой версией."""
    sid = _parse_uuid(set_id)
    parsed, key = _parse(file, settings, lon, lat, weight, category)
    with request.app.state.pool.connection() as conn:
        owner = conn.execute("SELECT namespace_id FROM point_set WHERE id = %s", (sid,)).fetchone()
        if owner is None or owner["namespace_id"] != principal.namespace_id:
            raise _not_found()
        vn = conn.execute(
            "SELECT coalesce(max(version_no), 0) + 1 AS vn FROM point_set_version "
            "WHERE set_id = %s",
            (sid,),
        ).fetchone()["vn"]
        conn.execute(
            "INSERT INTO point_set_version (id, set_id, version_no, count, snapshot_key) "
            "VALUES (%s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), sid, vn, parsed["count"], key),
        )
    return {"id": sid, "version_no": vn, "count": parsed["count"], "errors": parsed["errors"]}


@router.get("/{set_id}/heatmap/{tms}/{z}/{x}/{y}")
def point_heatmap(
    set_id: str,
    tms: str,
    z: int,
    x: int,
    y: str,
    request: Request,
    principal: ReadPrincipal,
    settings: SettingsDep,
    radius: float = 20,
    version: str | None = None,
) -> Response:
    """Тепловая карта с радиусом в пикселях (чисто визуальная, brief §3)."""
    sid = _parse_uuid(set_id)
    y_num = y.removesuffix(".png")
    if not y_num.isdigit():
        raise ApiError(404, "not_found", "Объект не найден")
    if tms not in heatmap.TMS:
        raise ApiError(404, "tms_not_found", "Тайловая схема не поддерживается")
    storage = get_storage(settings)
    with request.app.state.pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        owner = cur.execute("SELECT namespace_id FROM point_set WHERE id = %s", (sid,)).fetchone()
        if owner is None or owner["namespace_id"] != principal.namespace_id:
            raise _not_found()
        ver = cur.execute(
            "SELECT snapshot_key FROM point_set_version WHERE set_id = %s "
            "AND (%s::uuid IS NULL OR id = %s) ORDER BY version_no DESC LIMIT 1",
            (sid, version, version),
        ).fetchone()
        if ver is None:
            raise ApiError(404, "point_set_version_not_found", "Версия набора не найдена")
        snap = load_snapshot(storage, ver["snapshot_key"])
    png = heatmap.render_pixel_heatmap(
        snap["lon"], snap["lat"], snap["weight"], tms, z, x, int(y_num), radius
    )
    return Response(png, media_type="image/png")


@router.get("")
def list_point_sets(
    request: Request,
    principal: ReadPrincipal,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
) -> dict[str, Any]:
    with request.app.state.pool.connection() as conn:
        rows = (
            conn.cursor(row_factory=dict_row)
            .execute(
                "SELECT s.id, s.title, s.created_at, count(v.id) AS versions, "
                "coalesce(max(v.version_no), 0) AS latest_version_no "
                "FROM point_set s LEFT JOIN point_set_version v ON v.set_id = s.id "
                "WHERE s.namespace_id = %s GROUP BY s.id ORDER BY s.created_at LIMIT %s",
                (principal.namespace_id, min(limit, MAX_PAGE)),
            )
            .fetchall()
        )
    return {"items": [_public(r) for r in rows]}
