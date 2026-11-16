"""Загрузка файлов и импорт по манифесту (design §8; M1 — без inspect и загрузки по URL)."""

import hashlib
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError

from app.api.jobs import accepted_body
from app.api.service import SettingsDep
from app.auth import ReadPrincipal, WritePrincipal
from app.errors import ApiError
from app.importer.manifest import Manifest
from app.jobs import queue
from app.storage import get_storage

router = APIRouter(prefix="/v1", tags=["imports"])

READ_CHUNK = 1024 * 1024


def parse_uuid(value: str, error: ApiError) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise error from None


def _sniff(head: bytes) -> str | None:
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    return "geojson" if text.startswith(b"{") else None


def _too_large(limit: int) -> ApiError:
    return ApiError(413, "file_too_large", "Файл больше допустимого", {"max_bytes": limit})


def public_file(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "sha256": row["sha256"].hex(),
        "size": row["size"],
        "filename": row["filename"],
        "format": row["format"],
        "created_at": row["created_at"],
    }


@router.post("/files", status_code=201)
def upload_file(
    file: UploadFile, request: Request, principal: WritePrincipal, settings: SettingsDep
) -> JSONResponse:
    """Файл multipart (поле `file`). Тот же файл повторно — 200 и существующая запись."""
    limit = settings.max_upload_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + 64 * 1024:
        raise _too_large(limit)

    storage = get_storage(settings)
    file_id = str(uuid.uuid4())
    key = f"files/{file_id}"
    digest = hashlib.sha256()
    size = 0
    head = b""
    with storage.writer(key) as out:
        while chunk := file.file.read(READ_CHUNK):
            size += len(chunk)
            if size > limit:
                raise _too_large(limit)
            if len(head) < 64:
                head += chunk[:64]
            digest.update(chunk)
            out.write(chunk)
    sha = digest.digest()

    with request.app.state.pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        existing = cur.execute(
            "SELECT * FROM file WHERE namespace_id = %s AND sha256 = %s "
            "ORDER BY created_at LIMIT 1",
            (principal.namespace_id, sha),
        ).fetchone()
        if existing:
            storage.delete(key)
            return JSONResponse(_json(public_file(existing)), status_code=200)
        row = cur.execute(
            """
            INSERT INTO file
                (id, namespace_id, sha256, size, filename, format, storage_key, token_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
            """,
            (
                file_id,
                principal.namespace_id,
                sha,
                size,
                file.filename,
                _sniff(head),
                key,
                principal.token_id,
            ),
        ).fetchone()
    return JSONResponse(_json(public_file(row)), status_code=201)


def _json(body: dict[str, Any]) -> dict[str, Any]:
    return jsonable_encoder(body)  # type: ignore[no-any-return]


class ImportRequest(BaseModel):
    file_id: str
    manifest: dict[str, Any]
    callback_url: str | None = None


@router.post("/imports", status_code=202)
def create_import(
    body: ImportRequest,
    request: Request,
    principal: WritePrincipal,
    settings: SettingsDep,
    idempotency_key: Annotated[str | None, Header(max_length=200)] = None,
) -> JSONResponse:
    """Импорт файла по манифесту → 202 и задание. Тот же файл + манифест — 200, то же задание."""
    try:
        manifest = Manifest.model_validate(body.manifest)
    except ValidationError as exc:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ApiError(
            422, "manifest_invalid", "Манифест не прошёл проверку", {"errors": errors}
        ) from None
    file_id = parse_uuid(body.file_id, ApiError(404, "file_not_found", "Файл не найден"))
    manifest_hash = manifest.normalized_hash()

    with request.app.state.pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        f = cur.execute(
            "SELECT id, sha256 FROM file WHERE id = %s AND namespace_id = %s",
            (file_id, principal.namespace_id),
        ).fetchone()
        if f is None:
            raise ApiError(404, "file_not_found", "Файл не найден")
        created = cur.execute(
            """
            INSERT INTO import (namespace_id, file_id, file_sha256, manifest, manifest_hash)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (namespace_id, file_sha256, manifest_hash) DO NOTHING
            RETURNING id
            """,
            (
                principal.namespace_id,
                file_id,
                f["sha256"],
                Jsonb(manifest.model_dump(mode="json")),
                manifest_hash,
            ),
        ).fetchone()
        if created is None:
            # Дедупликация по содержимому (ADR-0009): тот же результат.
            existing = cur.execute(
                "SELECT i.id, j.id AS job_id, j.status FROM import i JOIN job j ON j.id = i.job_id "
                "WHERE i.namespace_id = %s AND i.file_sha256 = %s AND i.manifest_hash = %s",
                (principal.namespace_id, f["sha256"], manifest_hash),
            ).fetchone()
            assert existing is not None  # noqa: S101
            job = {"id": existing["job_id"], "status": existing["status"]}
            return JSONResponse(
                accepted_body(settings, job) | {"import_id": str(existing["id"])}, status_code=200
            )
        import_id = str(created["id"])
        payload = {"import_id": import_id}
        if body.callback_url:
            payload["callback_url"] = body.callback_url
        job = queue.enqueue(
            conn,
            "import",
            payload,
            namespace_id=principal.namespace_id,
            token_id=principal.token_id,
            idempotency_key=idempotency_key,
        )
        if job["payload"].get("import_id") != import_id:
            conn.rollback()
            raise ApiError(
                409, "idempotency_conflict", "Idempotency-Key уже использован с другим запросом"
            )
        conn.execute("UPDATE import SET job_id = %s WHERE id = %s", (job["id"], import_id))
    return JSONResponse(accepted_body(settings, job) | {"import_id": import_id}, status_code=202)


@router.get("/imports/{import_id}")
def get_import(import_id: str, request: Request, principal: ReadPrincipal) -> dict[str, Any]:
    not_found = ApiError(404, "import_not_found", "Импорт не найден")
    with request.app.state.pool.connection() as conn:
        row = (
            conn.cursor(row_factory=dict_row)
            .execute(
                """
            SELECT i.id, i.file_id, i.manifest, i.report, i.created_at, i.job_id, j.status
            FROM import i LEFT JOIN job j ON j.id = i.job_id
            WHERE i.id = %s AND i.namespace_id = %s
            """,
                (parse_uuid(import_id, not_found), principal.namespace_id),
            )
            .fetchone()
        )
    if row is None:
        raise not_found
    return {
        "id": str(row["id"]),
        "file_id": str(row["file_id"]),
        "job_id": str(row["job_id"]) if row["job_id"] else None,
        "status": row["status"],
        "manifest": row["manifest"],
        "report": row["report"],
        "created_at": row["created_at"],
    }
