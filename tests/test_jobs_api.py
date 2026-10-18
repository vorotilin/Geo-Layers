"""Ручки /v1/jobs: статус, отмена, повтор, доступ по namespace."""

from collections.abc import Iterator
from datetime import datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.api.jobs import accepted_body
from app.auth import hash_token
from app.config import Settings
from app.jobs import queue
from app.main import create_app

READ = {"Authorization": "Bearer read-t"}
WRITE = {"Authorization": "Bearer write-t"}
OTHER = {"Authorization": "Bearer other-t"}


@pytest.fixture
def client(db_url: str, db: psycopg.Connection) -> Iterator[TestClient]:
    db.execute("INSERT INTO namespace (id, title) VALUES ('other', 'Чужой')")
    for token, scope, ns in [
        ("read-t", "read", "shared"),
        ("write-t", "write", "shared"),
        ("other-t", "admin", "other"),
    ]:
        db.execute(
            "INSERT INTO token (hash, title, scopes, namespace_id) VALUES (%s, %s, %s, %s)",
            (hash_token(token), token, [scope], ns),
        )
    with TestClient(create_app(Settings(database_url=db_url))) as c:
        yield c


def new_job(db: psycopg.Connection) -> str:
    return str(queue.enqueue(db, "t", {"secret_input": 1}, namespace_id="shared")["id"])


def test_get_job(client: TestClient, db: psycopg.Connection) -> None:
    job_id = new_job(db)
    resp = client.get(f"/v1/jobs/{job_id}", headers=READ)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == job_id and body["status"] == "queued" and body["progress"] == 0
    assert "payload" not in body  # вход задания наружу не отдаём
    assert datetime.fromisoformat(body["created_at"]).utcoffset() is not None  # с таймзоной


@pytest.mark.parametrize("job_id", ["not-a-uuid", "00000000-0000-0000-0000-000000000000"])
def test_get_missing(client: TestClient, job_id: str) -> None:
    resp = client.get(f"/v1/jobs/{job_id}", headers=READ)
    assert resp.status_code == 404 and resp.json()["error"] == "job_not_found"


def test_other_namespace_sees_404(client: TestClient, db: psycopg.Connection) -> None:
    assert client.get(f"/v1/jobs/{new_job(db)}", headers=OTHER).status_code == 404


def test_requires_token_and_scope(client: TestClient, db: psycopg.Connection) -> None:
    job_id = new_job(db)
    assert client.get(f"/v1/jobs/{job_id}").status_code == 401
    assert client.post(f"/v1/jobs/{job_id}/cancel", headers=READ).status_code == 403


def test_cancel_then_retry(client: TestClient, db: psycopg.Connection) -> None:
    job_id = new_job(db)
    resp = client.post(f"/v1/jobs/{job_id}/cancel", headers=WRITE)
    assert resp.status_code == 200 and resp.json()["status"] == "cancelled"
    assert client.post(f"/v1/jobs/{job_id}/cancel", headers=WRITE).status_code == 200
    resp = client.post(f"/v1/jobs/{job_id}/retry", headers=WRITE)
    assert resp.status_code == 200 and resp.json()["status"] == "queued"
    resp = client.post(f"/v1/jobs/{job_id}/retry", headers=WRITE)
    assert resp.status_code == 409 and resp.json()["error"] == "job_not_retryable"


def test_cancel_finished_is_conflict(client: TestClient, db: psycopg.Connection) -> None:
    job_id = new_job(db)
    queue.claim(db, "w")
    queue.complete(db, job_id, "w", {"n": 1})
    resp = client.post(f"/v1/jobs/{job_id}/cancel", headers=WRITE)
    assert resp.status_code == 409
    assert resp.json()["details"] == {"status": "succeeded"}
    assert client.get(f"/v1/jobs/{job_id}", headers=READ).json()["result"] == {"n": 1}


def test_failed_job_shows_error(client: TestClient, db: psycopg.Connection) -> None:
    job_id = new_job(db)
    queue.claim(db, "w")
    err = {"error": "bad_file", "message": "Файл не читается", "details": {}}
    queue.fail(db, job_id, "w", err, retryable=False, delay=timedelta(0))
    body = client.get(f"/v1/jobs/{job_id}", headers=READ).json()
    assert body["status"] == "failed" and body["error"] == err


def test_accepted_body() -> None:
    settings = Settings(public_base_url="https://layers.example/")
    body = accepted_body(settings, {"id": "abc", "status": "queued"})
    assert body == {
        "job_id": "abc",
        "status": "queued",
        "status_url": "https://layers.example/v1/jobs/abc",
    }
