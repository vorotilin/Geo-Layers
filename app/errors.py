"""Единая форма ошибок: {"error": slug, "message": текст, "details": {...}}.

common-requirements §2: слаги латиницей в snake_case, тексты по-русски.
"""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

_DEFAULT_SLUGS = {
    400: ("bad_request", "Некорректный запрос"),
    401: ("unauthorized", "Нет токена или токен недействителен"),
    403: ("forbidden", "Токену это действие запрещено"),
    404: ("not_found", "Объект не найден"),
    405: ("method_not_allowed", "Метод не поддерживается"),
    409: ("conflict", "Конфликт состояния"),
    413: ("payload_too_large", "Слишком большой файл"),
    422: ("unprocessable", "Запрос не прошёл проверку"),
    429: ("rate_limited", "Превышен лимит запросов"),
    503: ("unavailable", "Сервис временно недоступен"),
}


class ApiError(Exception):
    """Ошибка API с машинным слагом. Бросается из кода ручек и сервисов."""

    def __init__(
        self,
        status: int,
        error: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error = error
        self.message = message
        self.details = details or {}
        self.headers = headers


def error_response(
    status: int,
    error: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": error, "message": message, "details": details or {}},
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.error, exc.message, exc.details, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        slug, message = _DEFAULT_SLUGS.get(
            exc.status_code, ("http_error", HTTPStatus(exc.status_code).phrase)
        )
        if isinstance(exc.detail, str) and exc.detail != HTTPStatus(exc.status_code).phrase:
            message = exc.detail
        return error_response(exc.status_code, slug, message, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "type": e.get("type"), "msg": e.get("msg")}
            for e in exc.errors()
        ]
        return error_response(
            422, "validation_error", "Запрос не прошёл проверку", {"errors": errors}
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return error_response(500, "internal_error", "Внутренняя ошибка сервиса")
