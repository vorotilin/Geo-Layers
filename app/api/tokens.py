"""Токены API: выпуск, список, отзыв (common-requirements §3, brief §9).

Секрет показывается один раз при выпуске; в списке — только метаданные.
Токен управляет только токенами своего namespace (граница приватности).
"""

import secrets
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from app.auth import AdminPrincipal, hash_token
from app.errors import ApiError

router = APIRouter(prefix="/v1/tokens", tags=["tokens"])

SCOPES = ("admin", "write", "read")
MAX_PAGE = 200


def _not_found() -> ApiError:
    return ApiError(404, "token_not_found", "Токен не найден")


class CreateToken(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(min_length=1, max_length=3)


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "scopes": row["scopes"],
        "namespace_id": row["namespace_id"],
        "created_at": row["created_at"],
        "revoked_at": row["revoked_at"],
    }


@router.post("", status_code=201)
def create_token(body: CreateToken, request: Request, principal: AdminPrincipal) -> dict[str, Any]:
    scopes = sorted(set(body.scopes))
    if not scopes or not set(scopes) <= set(SCOPES):
        raise ApiError(422, "token_invalid", "Недопустимые области", {"allowed": list(SCOPES)})
    token = secrets.token_urlsafe(32)
    with request.app.state.pool.connection() as conn:
        row = (
            conn.cursor(row_factory=dict_row)
            .execute(
                "INSERT INTO token (hash, title, scopes, namespace_id) "
                "VALUES (%s, %s, %s, %s) "
                "RETURNING id, title, scopes, namespace_id, created_at, revoked_at",
                (hash_token(token), body.title, scopes, principal.namespace_id),
            )
            .fetchone()
        )
    assert row is not None  # noqa: S101 — RETURNING всегда даёт строку
    return _public(row) | {"token": token}  # секрет — только здесь, один раз


@router.get("")
def list_tokens(
    request: Request,
    principal: AdminPrincipal,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
) -> dict[str, Any]:
    with request.app.state.pool.connection() as conn:
        rows = (
            conn.cursor(row_factory=dict_row)
            .execute(
                "SELECT id, title, scopes, namespace_id, created_at, revoked_at "
                "FROM token WHERE namespace_id = %s ORDER BY created_at LIMIT %s",
                (principal.namespace_id, limit),
            )
            .fetchall()
        )
    return {"items": [_public(r) for r in rows]}


@router.delete("/{token_id}")
def revoke_token(token_id: str, request: Request, principal: AdminPrincipal) -> dict[str, Any]:
    try:
        tid = str(uuid.UUID(token_id))
    except ValueError:
        raise _not_found() from None
    with request.app.state.pool.connection() as conn:
        row = (
            conn.cursor(row_factory=dict_row)
            .execute(
                "UPDATE token SET revoked_at = now() WHERE id = %s AND namespace_id = %s "
                "RETURNING id, title, scopes, namespace_id, created_at, revoked_at",
                (tid, principal.namespace_id),
            )
            .fetchone()
        )
    if row is None:
        raise _not_found()
    return _public(row)
