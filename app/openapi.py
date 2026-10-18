"""OpenAPI 3.1 из кода + то, чего FastAPI сам не знает (common-requirements §2).

`openapi.yaml` в корне генерируется отсюда (`python -m app.openapi > openapi.yaml`);
тест сверяет файл с кодом, поэтому контракт и реализация не расходятся.
"""

import sys
from typing import Any

import yaml
from fastapi import FastAPI

ERROR_SCHEMA = {
    "type": "object",
    "required": ["error", "message", "details"],
    "properties": {
        "error": {
            "type": "string",
            "description": "Машинный слаг, snake_case",
            "examples": ["job_not_found"],
        },
        "message": {
            "type": "string",
            "description": "Текст для человека",
            "examples": ["Задание не найдено"],
        },
        "details": {"type": "object"},
    },
}

DESCRIPTION = """\
Сервис гео-слоёв платформы наружной рекламы. Черновик контракта этапа M1.

- Аутентификация: `Authorization: Bearer <token>`, области `admin ⊃ write ⊃ read`.
- Тайлы — по подписанной ссылке из `POST /v1/tiles/sign` (подпись префикса пути, ADR-0008).
- Долгие операции — задания: `202` + `status_url`, затем `GET /v1/jobs/{id}`.
- Идемпотентность: `Idempotency-Key` у `POST /v1/imports` и дедупликация импорта
  по sha256 файла + хэшу нормализованного манифеста.
- Ошибки — `{error, message, details}`; неизвестные поля игнорируются, перечисления открытые.
"""

PUBLIC = {"/health", "/ready", "/v1/tiles/{layer_id}/{version_id}/{style}/{tms}/{z}/{x}/{y}"}


def build(app: FastAPI) -> dict[str, Any]:
    app.openapi_schema = None
    schema = app.openapi()
    schema["info"]["description"] = DESCRIPTION
    schema["info"]["version"] = "m1-draft"
    comps = schema.setdefault("components", {})
    comps.setdefault("securitySchemes", {})["bearer"] = {"type": "http", "scheme": "bearer"}
    comps.setdefault("schemas", {})["Error"] = ERROR_SCHEMA
    error_ref = {
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}
    }
    for path, ops in schema["paths"].items():
        for op in ops.values():
            if path not in PUBLIC:
                op["security"] = [{"bearer": []}]
                op["responses"].setdefault(
                    "401", {"description": "Нет токена или он недействителен"} | error_ref
                )
            op["responses"].pop("422", None)
            op["responses"]["4XX"] = {"description": "Ошибка запроса"} | error_ref
    tile = (
        schema["paths"]
        .get("/v1/tiles/{layer_id}/{version_id}/{style}/{tms}/{z}/{x}/{y}", {})
        .get("get")
    )
    if tile:
        tile["description"] = (
            "Тайл PNG 256×256 (`{y}.png`) или 512×512 (`{y}@2x.png`). `tms`: `3857` | `3395`. "
            "Доступ — по `exp` и `sig` из `POST /v1/tiles/sign` или Bearer-токену read."
        )
        tile["responses"]["200"] = {"description": "PNG", "content": {"image/png": {}}}
    return schema


def dump(schema: dict[str, Any]) -> str:
    return yaml.safe_dump(schema, allow_unicode=True, sort_keys=False, width=100)


if __name__ == "__main__":
    from app.config import Settings
    from app.main import create_app

    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stdout.write(dump(build(create_app(Settings(app_env="dev")))))
