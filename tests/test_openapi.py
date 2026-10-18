"""openapi.yaml в репозитории совпадает с кодом (common-requirements §2)."""

from pathlib import Path

from app.config import Settings
from app.main import create_app
from app.openapi import build, dump

SPEC = Path(__file__).resolve().parent.parent / "openapi.yaml"


def test_openapi_yaml_matches_code() -> None:
    expected = dump(build(create_app(Settings(app_env="dev"))))
    actual = SPEC.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert actual == expected, "openapi.yaml устарел: python -m app.openapi > openapi.yaml"


def test_openapi_covers_m1_routes() -> None:
    paths = build(create_app(Settings(app_env="dev")))["paths"]
    for path in (
        "/v1/files",
        "/v1/imports",
        "/v1/jobs/{job_id}",
        "/v1/layers",
        "/v1/values",
        "/v1/tiles/sign",
        "/v1/layers/{layer_id}/versions/{version_id}/publish",
    ):
        assert path in paths
    assert paths["/v1/values"]["get"]["security"] == [{"bearer": []}]
    assert "security" not in paths["/health"]["get"]
