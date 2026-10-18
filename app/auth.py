"""Bearer-токены: хэш sha256 в таблице `token`, области admin | write | read.

common-requirements §3, design §9. На M1 один namespace (`shared`) и bootstrap-токен
из env; выпуск и отзыв через /v1/tokens — M2.
"""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

import psycopg
from fastapi import Depends, Request
from psycopg.rows import tuple_row

from app.db import SHARED_NAMESPACE
from app.errors import ApiError

log = logging.getLogger(__name__)

# admin может всё, write включает read.
_IMPLIED = {"admin": {"admin", "write", "read"}, "write": {"write", "read"}, "read": {"read"}}


@dataclass(frozen=True)
class Principal:
    token_id: str
    namespace_id: str
    scopes: frozenset[str]

    def can(self, scope: str) -> bool:
        return any(scope in _IMPLIED.get(s, set()) for s in self.scopes)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def ensure_bootstrap_token(conn: psycopg.Connection[Any], token: str | None) -> bool:
    """Создаёт admin-токен из BOOTSTRAP_ADMIN_TOKEN, только если таблица токенов пуста."""
    if not token:
        return False
    with conn.transaction():
        # Блокировка таблицы: два migrate одновременно не создадут два токена.
        conn.execute("LOCK TABLE token IN SHARE ROW EXCLUSIVE MODE")
        if conn.execute("SELECT 1 FROM token LIMIT 1").fetchone():
            return False
        conn.execute(
            "INSERT INTO token (hash, title, scopes, namespace_id) VALUES (%s, %s, %s, %s)",
            (hash_token(token), "bootstrap admin", ["admin"], SHARED_NAMESPACE),
        )
    log.info("bootstrap admin token created")
    return True


def find_principal(conn: psycopg.Connection[Any], token: str) -> Principal | None:
    with conn.cursor(row_factory=tuple_row) as cur:
        row = cur.execute(
            "SELECT id, namespace_id, scopes FROM token WHERE hash = %s AND revoked_at IS NULL",
            (hash_token(token),),
        ).fetchone()
    if row is None:
        return None
    token_id, namespace_id, scopes = row
    return Principal(str(token_id), namespace_id, frozenset(scopes))


def _unauthorized() -> ApiError:
    return ApiError(
        401,
        "unauthorized",
        "Нет токена или токен недействителен",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized()
    return token.strip()


def require_scope(scope: str) -> Callable[[Request], Principal]:
    """Зависимость FastAPI: токен действителен и имеет область `scope`."""

    def dependency(request: Request) -> Principal:
        token = _bearer(request)
        with request.app.state.pool.connection() as conn:
            principal = find_principal(conn, token)
        if principal is None:
            raise _unauthorized()
        if not principal.can(scope):
            raise ApiError(403, "forbidden", "Токену это действие запрещено", {"scope": scope})
        return principal

    return dependency


ReadPrincipal = Annotated[Principal, Depends(require_scope("read"))]
WritePrincipal = Annotated[Principal, Depends(require_scope("write"))]
AdminPrincipal = Annotated[Principal, Depends(require_scope("admin"))]
