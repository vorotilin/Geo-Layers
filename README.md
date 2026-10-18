# apc-layers — сервис гео-слоёв

Сервис платформы наружной рекламы. Принимает файлы сеток данных поставщиков
и наборы точек, готовит из них растровые тайлы, легенды, статистику и
агрегаты, отдаёт всё по HTTP.

Платформа вызывает сервис по HTTP и получает результат сразу либо позже
через callback. Сервис не ходит в базу платформы и не принимает бизнес-решений.

## Статус

Этап **M1. Вертикальный срез** — в работе, план в `docs/plan-m1.md`.
Готово: каркас (`/health`, `/ready`, Docker, CI), схема БД и Bearer-токены, импорт GeoJSON, публикация, значения, подписанные тайлы 3857/3395, `openapi.yaml`, веб-интерфейс `/ui` и первый замер тайлов. План M1 выполнен (шаги 1–11).

## Документация

- `docs/design.md` — design doc: границы сервиса, черновик API, схема данных,
  стек с обоснованием, риски, план этапов.
- `docs/decisions.md` — принятые решения (мини-ADR).
- `docs/questions.md` — открытые вопросы заказчику.
- `docs/known-issues.md` — известные ограничения и долги.
- `docs/how-to-check.md` — как самому запустить и проверить M1 (Windows, PowerShell).
- `brief.md` — задание сервиса.

## Стек (кратко)

Python 3.12 + FastAPI, PostgreSQL + PostGIS, Redis, S3 (MinIO локально),
веб-интерфейс на Vue 3 (JavaScript, Vite) + MapLibre GL JS. Обоснование — `docs/decisions.md`.

## Требования

Общие требования к сервисам платформы — в `common-requirements.md`
репозитория `apc-microservices`. Задание сервиса — в `01-geo-layers/brief.md`
того же репозитория.

## Запуск за 10 минут

Нужен Docker с Compose v2 и Python 3.10+ (только для проверочного скрипта).

```sh
docker compose up -d --build --wait      # api, worker, PostGIS, миграции
curl http://localhost:8000/health        # жив, версия сборки
curl http://localhost:8000/ready         # зависимости доступны
python scripts/e2e.py                    # сквозной сценарий на синтетике
```

`scripts/e2e.py` проходит весь сценарий M1 и сверяет ответы с заранее известными:
загрузка файла → импорт заданием → публикация → значение в точке → подписанный
тайл PNG в EPSG:3857 и EPSG:3395. С образцом поставщика (положите
`layers_sample_2023Q1.zip` в `data/`, в git он не попадает):

```sh
python scripts/e2e.py --sample data/layers_sample_2023Q1.zip
```

Веб-интерфейс — `http://localhost:8000/ui/`, вход по токену. Swagger UI —
`http://localhost:8000/docs` (только при `APP_ENV=dev`). Контракт — `openapi.yaml`.

Файл `.env` не обязателен: у всех переменных в `docker-compose.yml` есть значения
для локального запуска (только для разработки). Токен администратора в dev —
`dev-admin-token` (`BOOTSTRAP_ADMIN_TOKEN`).

Сервисы на M1: `api`, `worker` (тот же образ), `postgres` (PostGIS) и одноразовый
`migrate` (миграции БД и bootstrap-токен). Данные — в volume `data`, кэш тайлов —
в volume `cache`. S3 и Redis добавятся на M2.

### Сценарий вручную

```sh
T="Authorization: Bearer dev-admin-token"
# 1. Файл → file_id
curl -H "$T" -F file=@mean_income_home_2023Q1.geojson http://localhost:8000/v1/files
# 2. Импорт по манифесту → job_id, status_url
curl -H "$T" -H "Content-Type: application/json" http://localhost:8000/v1/imports -d '{
  "file_id": "<file_id>",
  "manifest": {"kind": "grid", "dataset": {"provider": "sample", "title": "Москва 500 м"},
    "period": "2023Q1", "geometry": {"type": "geojson"}, "key": "zone_id",
    "metrics": [{"column": "val", "slug": "mean_income_home", "title": "Средний доход",
                 "unit": "руб.", "value_kind": "intensive"}]}}'
# 3. Статус задания; в result — layer_id и version_id черновика
curl -H "$T" http://localhost:8000/v1/jobs/<job_id>
# 4. Публикация
curl -H "$T" -X POST http://localhost:8000/v1/layers/<layer_id>/versions/<version_id>/publish
# 5. Значение в точке
curl -H "$T" "http://localhost:8000/v1/values?lat=55.75&lng=37.62&layers=<layer_id>"
# 6. Подписанный шаблон URL тайлов (tms: 3857 | 3395)
curl -H "$T" -H "Content-Type: application/json" http://localhost:8000/v1/tiles/sign   -d '{"layer": "<layer_id>", "tms": "3857"}'
```

### Контракт API

`openapi.yaml` генерируется из кода и проверяется тестом:

```sh
uv run python -m app.openapi > openapi.yaml
```

### Разработка без Docker

```sh
pip install uv==0.13.0
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy app tests && uv run pytest
```

Тесты с БД нужен PostGIS, иначе они пропускаются. Схема `public` тестовой базы
пересоздаётся — не указывайте рабочую базу:

```sh
docker run -d --name apc-test-pg -p 55432:5432 -e POSTGRES_DB=apc_test   -e POSTGRES_USER=apc -e POSTGRES_PASSWORD=apc postgis/postgis:16-3.4
TEST_DATABASE_URL=postgresql://apc:apc@127.0.0.1:55432/apc_test uv run pytest
```

Миграции: `uv run alembic upgrade head` (берёт `DATABASE_URL`), новая ревизия —
`uv run alembic revision -m "..."`; SQL пишем вручную, автогенерации нет.

## Конфигурация

Все переменные окружения — в `.env.example` с комментариями.

## Лицензии

Лицензии зависимостей должны быть совместимы с закрытым коммерческим
использованием. Внешние бинари (ffmpeg, GDAL) — вызовом процесса.