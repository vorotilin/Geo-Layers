"""Задания: статус, отмена, повтор (common-requirements §2, design §8)."""

import uuid
from typing import Any

from fastapi import APIRouter, Request

from app.auth import ReadPrincipal, WritePrincipal
from app.config import Settings
from app.errors import ApiError
from app.jobs import queue
from app.jobs.queue import Job

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


def status_url(settings: Settings, job_id: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/v1/jobs/{job_id}"


def accepted_body(settings: Settings, job: Job) -> dict[str, Any]:
    """Тело ответа 202 для ручек, которые ставят задание."""
    job_id = str(job["id"])
    return {"job_id": job_id, "status": job["status"], "status_url": status_url(settings, job_id)}


def public_job(job: Job) -> dict[str, Any]:
    return {
        "id": str(job["id"]),
        "type": job["type"],
        "status": job["status"],
        "progress": round(job["progress"], 1),
        "attempts": job["attempts"],
        "max_attempts": job["max_attempts"],
        "result": job["result"],
        "error": job["error"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "updated_at": job["updated_at"],
    }


def _not_found() -> ApiError:
    return ApiError(404, "job_not_found", "Задание не найдено")


def _parse_id(job_id: str) -> str:
    try:
        return str(uuid.UUID(job_id))
    except ValueError:
        raise _not_found() from None


@router.get("/{job_id}")
def get_job(job_id: str, request: Request, principal: ReadPrincipal) -> dict[str, Any]:
    with request.app.state.pool.connection() as conn:
        job = queue.get(conn, _parse_id(job_id), principal.namespace_id)
    if job is None:
        raise _not_found()
    return public_job(job)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, principal: WritePrincipal) -> dict[str, Any]:
    """Отмена queued/running. Повторная отмена — 200, завершённое задание — 409."""
    with request.app.state.pool.connection() as conn:
        job, changed = queue.cancel(conn, _parse_id(job_id), principal.namespace_id)
    if job is None:
        raise _not_found()
    if not changed and job["status"] != "cancelled":
        raise ApiError(409, "job_finished", "Задание уже завершено", {"status": job["status"]})
    return public_job(job)


@router.post("/{job_id}/retry")
def retry_job(job_id: str, request: Request, principal: WritePrincipal) -> dict[str, Any]:
    """Повтор failed/cancelled с нуля попыток. Иначе — 409."""
    with request.app.state.pool.connection() as conn:
        job, changed = queue.retry(conn, _parse_id(job_id), principal.namespace_id)
    if job is None:
        raise _not_found()
    if not changed:
        raise ApiError(
            409,
            "job_not_retryable",
            "Повторить можно только упавшее или отменённое задание",
            {"status": job["status"]},
        )
    return public_job(job)
