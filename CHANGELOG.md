# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).

## [Unreleased]

### Добавлено
- M1, шаги 9–10. Сквозной сценарий `scripts/e2e.py` в CI после `docker compose up`.
  `GET /v1/layers` — каталог слоёв с курсором. `openapi.yaml` (OpenAPI 3.1) генерируется
  из кода (`python -m app.openapi`), тест сверяет файл с кодом. README: запуск за 10 минут.
- M1, шаги 6–8. Генератор синтетики `tests/synth/` (квадраты CRS84 с «плавающим» шагом,
  квадраты в UTM, дыры, битые объекты; оракул на shapely).
- Импорт GeoJSON-сетки: `POST /v1/files` (multipart, sha256, дедупликация файла),
  `POST /v1/imports` (манифест, дедупликация по файлу и манифесту, `Idempotency-Key`),
  `GET /v1/imports/{id}` с отчётом об ошибках. Парсер — потоково (ijson) в подпроцессе
  с лимитами памяти и времени; распознавание сетки `rectilinear`; одна сетка на
  поставщика, новые ячейки дописываются; массивы `float32[N]` и статистика версии.
- `GET /v1/layers/{id}`, `GET …/versions/{v}`, `POST …/versions/{v}/publish`,
  `GET /v1/values` — значение в точке по слоям.
- Тайлы PNG в EPSG:3857 и EPSG:3395 (morecantile): тайл индексов сетки с кэшем,
  квантильная палитра, `@2x`, дисковый кэш, `ETag`/`304`, `immutable`;
  `POST /v1/tiles/sign` — подпись префикса пути (ADR-0008).
- `scripts/e2e.py` — сквозной сценарий для CI и приёмки (только stdlib).
- M1, шаг 5: очередь заданий на таблице `job` (ADR-0003, `app/jobs/`): захват
  `FOR UPDATE SKIP LOCKED`, heartbeat в отдельном потоке, подбор зависших по
  `heartbeat_at`, повтор с растущей задержкой, `NOTIFY` для мгновенного старта,
  `Idempotency-Key` при постановке. По SIGTERM задание возвращается в очередь
  без потери попытки.
- Ручки `GET /v1/jobs/{id}`, `POST /v1/jobs/{id}/cancel`, `POST /v1/jobs/{id}/retry`.
- Настройки очереди: `JOB_HEARTBEAT_SECONDS`, `JOB_STALE_SECONDS`,
  `JOB_RETRY_BASE_SECONDS`, `JOB_RETRY_MAX_SECONDS`, `WORKER_POLL_SECONDS`.
- M1, шаг 4: схема БД на Alembic (`app/db/migrations`, ревизия `0001`): namespace,
  token, file, job, import, dataset, grid, grid_cell (PostGIS, GiST), layer, layer_version.
  Миграции применяет `python -m app migrate` (в compose — сервис `migrate`) под advisory lock.
- Bearer-токены: хэш sha256 в БД, области `admin ⊃ write ⊃ read`, отзыв через `revoked_at`;
  admin-токен из `BOOTSTRAP_ADMIN_TOKEN` создаётся при пустой таблице.
- Тесты с БД (`TEST_DATABASE_URL`, PostGIS); в CI — сервис postgis.
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
- Healthcheck postgres в compose и CI — по TCP: при первой инициализации образ
  postgis перезапускается, и проверка по сокету давала ложное «готов».
- Valkey заменён на Redis 8; Redis используется только для rate limit, кэш тайлов — на диске.
- Проекции 3857/3395 берутся из morecantile вместо собственных формул (ADR-0006).
- Запасной план по скорости рендера: оптимизация numpy, затем сервис на Go (вместо Rust).
- Статус задания `cancelled`; Swagger UI на `/docs` только при `APP_ENV=dev`.
- `README.Md` переименован в `README.md`.
