"""Стили слоёв: создание, список, новая ревизия; резолвинг для тайлов и легенды.

Стиль неизменяем: изменение создаёт новую ревизию (`name@revision`), ревизия входит
в URL тайла (ADR-0005). Встроенный стиль `default` — BUILTIN_STYLE (7 квантильных
классов), в БД не хранится.
"""

import re
import uuid
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Query, Request
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, ValidationError

from app.auth import ReadPrincipal, WritePrincipal
from app.errors import ApiError
from app.styles import BUILTIN_STYLE, Style

router = APIRouter(prefix="/v1", tags=["styles"])

DEFAULT_STYLE = "default"
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
MAX_PAGE = 200


def _not_found() -> ApiError:
    return ApiError(404, "style_not_found", "Стиль не найден")


def _parse_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise _not_found() from None


def _validate_spec(spec: dict[str, Any]) -> dict[str, Any]:
    try:
        return Style.model_validate(spec).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise ApiError(
            422,
            "style_invalid",
            "Спецификация стиля не прошла проверку",
            {"errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]},
        ) from None


def resolve_style(
    conn: psycopg.Connection[Any], namespace: str, ref: str, *, latest: bool = False
) -> tuple[Style | None, str | None]:
    """(Style, канонический `name@revision`). `default` → (BUILTIN_STYLE, "default").

    `name@revision` — строгая ревизия (для тайлов, где URL неизменяем);
    `latest=True` разрешает `name` в последнюю ревизию (для легенды и подписи).
    Не найдено — (None, None).
    """
    if ref == DEFAULT_STYLE:
        return BUILTIN_STYLE, DEFAULT_STYLE
    name, _, rev = ref.partition("@")
    if not NAME.match(name):
        return None, None
    if not rev and not latest:
        return None, None
    cur = conn.cursor(row_factory=tuple_row)
    if rev:
        row = cur.execute(
            "SELECT spec, revision FROM style WHERE namespace_id = %s AND name = %s "
            "AND revision = %s",
            (namespace, name, rev),
        ).fetchone()
    else:
        row = cur.execute(
            "SELECT spec, revision FROM style WHERE namespace_id = %s AND name = %s "
            "ORDER BY revision DESC LIMIT 1",
            (namespace, name),
        ).fetchone()
    if row is None:
        return None, None
    return Style.model_validate(row[0]), f"{name}@{row[1]}"


class CreateStyle(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    spec: dict[str, Any]


class UpdateStyle(BaseModel):
    spec: dict[str, Any]


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "revision": row["revision"],
        "spec": row["spec"],
        "created_at": row["created_at"],
    }


@router.post("/styles", status_code=201)
def create_style(body: CreateStyle, request: Request, principal: WritePrincipal) -> dict[str, Any]:
    if not NAME.match(body.name):
        raise ApiError(422, "style_invalid", "Недопустимое имя стиля", {"name": body.name})
    spec = _validate_spec(body.spec)
    with request.app.state.pool.connection() as conn:
        row = (
            conn.cursor(row_factory=dict_row)
            .execute(
                "INSERT INTO style (namespace_id, name, spec, revision) "
                "VALUES (%s, %s, %s, 1) RETURNING *",
                (principal.namespace_id, body.name, Jsonb(spec)),
            )
            .fetchone()
        )
    assert row is not None  # noqa: S101 — RETURNING всегда даёт строку
    return _public(row)


@router.get("/styles")
def list_styles(
    request: Request,
    principal: ReadPrincipal,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
) -> dict[str, Any]:
    """Имена стилей с последней ревизией (каталог небольшой, без курсора)."""
    with request.app.state.pool.connection() as conn:
        rows = (
            conn.cursor(row_factory=dict_row)
            .execute(
                "SELECT DISTINCT ON (name) id, name, revision, spec, created_at "
                "FROM style WHERE namespace_id = %s ORDER BY name, revision DESC LIMIT %s",
                (principal.namespace_id, limit),
            )
            .fetchall()
        )
    return {"items": [_public(r) for r in rows]}


@router.put("/styles/{style_id}")
def update_style(
    style_id: str, body: UpdateStyle, request: Request, principal: WritePrincipal
) -> dict[str, Any]:
    """Новая ревизия существующего стиля (старая остаётся по `name@rev`)."""
    spec = _validate_spec(body.spec)
    with request.app.state.pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        current = cur.execute(
            "SELECT namespace_id, name FROM style WHERE id = %s", (_parse_uuid(style_id),)
        ).fetchone()
        if current is None or current["namespace_id"] != principal.namespace_id:
            raise _not_found()
        row = cur.execute(
            "INSERT INTO style (namespace_id, name, spec, revision) "
            "SELECT namespace_id, name, %s, max(revision) + 1 FROM style WHERE id = %s "
            "GROUP BY namespace_id, name RETURNING *",
            (Jsonb(spec), _parse_uuid(style_id)),
        ).fetchone()
    assert row is not None  # noqa: S101 — RETURNING всегда даёт строку
    return _public(row)
