"""Служебные ручки, форма ошибок, X-Request-Id."""

import pytest
from fastapi.testclient import TestClient

from app import readiness
from app.config import Settings
from app.main import create_app


def make_client(**overrides: object) -> TestClient:
    settings = Settings(app_version="test-1", **overrides)  # type: ignore[arg-type]
    return TestClient(create_app(settings))


def test_health_returns_version() -> None:
    resp = make_client().get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "test-1"}


def test_request_id_is_echoed() -> None:
    resp = make_client().get("/health", headers={"X-Request-Id": "abc-123"})
    assert resp.headers["X-Request-Id"] == "abc-123"


def test_request_id_generated_when_missing_or_invalid() -> None:
    client = make_client()
    assert len(client.get("/health").headers["X-Request-Id"]) == 32
    bad = client.get("/health", headers={"X-Request-Id": "a b\tc"})
    assert bad.headers["X-Request-Id"] != "a b\tc"


def test_unknown_route_uses_error_shape() -> None:
    resp = make_client().get("/v1/nope")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"] == "not_found"
    assert isinstance(body["message"], str)
    assert body["details"] == {}


@pytest.mark.parametrize(("env", "status"), [("dev", 200), ("prod", 404)])
def test_docs_only_in_dev(env: str, status: int) -> None:
    assert make_client(app_env=env).get("/docs").status_code == status


def test_ready_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        readiness, "CHECKS", {"database": lambda s: None, "storage": lambda s: None}
    )
    resp = make_client().get("/ready")
    assert resp.status_code == 200
    assert resp.json()["checks"] == {"database": "ok", "storage": "ok"}


def test_ready_fails_when_dependency_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_: Settings) -> None:
        raise ConnectionError("down")

    monkeypatch.setattr(readiness, "CHECKS", {"database": broken, "storage": lambda s: None})
    resp = make_client().get("/ready")
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "5"
    body = resp.json()
    assert body["error"] == "not_ready"
    assert body["details"]["checks"] == {"database": "fail", "storage": "ok"}


def test_ui_served_when_built(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "index.html").write_text("<html>ui</html>", encoding="utf-8")
    client = make_client(ui_dir=str(tmp_path))
    assert client.get("/ui", follow_redirects=False).headers["location"] == "/ui/"
    resp = client.get("/ui/")
    assert resp.status_code == 200 and "ui" in resp.text
    assert make_client(ui_dir=str(tmp_path / "none")).get("/ui/").status_code == 404
