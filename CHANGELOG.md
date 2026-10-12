# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).

## [Unreleased]

### Добавлено
- M1, каркас: приложение FastAPI (`app/`), `/health`, `/ready` (БД + хранилище),
  единая форма ошибок, `X-Request-Id`, JSON-логи без query-строк; Swagger UI только в dev.
- Один образ для ролей `api | worker | migrate` (multi-stage, не root, `HEALTHCHECK`),
  `docker-compose.yml` (api, worker, PostGIS), зависимости в `uv.lock`.
- CI: ruff, mypy, pytest, gitleaks, сборка и запуск compose с проверкой `/health`, `/ready`.
- План M1 (`docs/plan-m1.md`). На M1 без S3 и Redis: хранилище — volume `DATA_DIR`.
- M0: design doc (`docs/design.md`), решения ADR-0001…0010, вопросы заказчику
  (`docs/questions.md`, №1–3), известные ограничения (`docs/known-issues.md`, KI-1…KI-9).
- ADR-0010: веб-интерфейс на Vue 3 (JavaScript) + Vite + MapLibre GL JS.
- Тестирование на синтетических данных с заранее известным ответом; образец —
  только один из прогонов (`docs/design.md` §12).
- Ручка `DELETE /v1/point-sets/{id}` и срок хранения наборов точек
  (`POINT_SETS_RETENTION_DAYS`).
- Настройка `SIGN_EXP_ROUNDING_SECONDS` — шаг округления срока подписи (по умолчанию 1 час).
- `common-requirements.md` — общие требования к сервисам платформы.

### Изменено
- Valkey заменён на Redis 8; Redis используется только для rate limit, кэш тайлов — на диске.
- Проекции 3857/3395 берутся из morecantile вместо собственных формул (ADR-0006).
- Запасной план по скорости рендера: оптимизация numpy, затем сервис на Go (вместо Rust).
- Статус задания `cancelled`; Swagger UI на `/docs` только при `APP_ENV=dev`.
- `README.Md` переименован в `README.md`.
