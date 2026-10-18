# apc-layers — сервис гео-слоёв

Сервис платформы наружной рекламы. Принимает файлы сеток данных поставщиков
и наборы точек, готовит из них растровые тайлы, легенды, статистику и
агрегаты, отдаёт всё по HTTP.

Платформа вызывает сервис по HTTP и получает результат сразу либо позже
через callback. Сервис не ходит в базу платформы и не принимает бизнес-решений.

## Статус

Этап **M1. Вертикальный срез** — в работе, план в `docs/plan-m1.md`.
Готово: каркас (`/health`, `/ready`, Docker, CI), схема БД и Bearer-токены, очередь заданий, импорт GeoJSON, публикация, значения и тайлы (шаги 1–8).

## Документация

- `docs/design.md` — design doc: границы сервиса, черновик API, схема данных,
  стек с обоснованием, риски, план этапов.
- `docs/decisions.md` — принятые решения (мини-ADR).
- `docs/questions.md` — открытые вопросы заказчику.
- `docs/known-issues.md` — известные ограничения и долги.
- `brief.md` — задание сервиса.

## Стек (кратко)

Python 3.12 + FastAPI, PostgreSQL + PostGIS, Redis, S3 (MinIO локально),
веб-интерфейс на Vue 3 (JavaScript, Vite) + MapLibre GL JS. Обоснование — `docs/decisions.md`.

## Требования

Общие требования к сервисам платформы — в `common-requirements.md`
репозитория `apc-microservices`. Задание сервиса — в `01-geo-layers/brief.md`
того же репозитория.

## Запуск

Нужен Docker с Compose v2.

Полный образец `layers_sample_2023Q1.zip` положите в `data/` (в git не попадает).

```sh
docker compose up -d --build --wait
curl http://localhost:8000/health   # жив, версия сборки
curl http://localhost:8000/ready    # зависимости доступны
```

Файл `.env` не обязателен: у всех переменных в `docker-compose.yml` есть значения
для локального запуска (только для разработки). Swagger UI — `http://localhost:8000/docs`
(только при `APP_ENV=dev`).

Сервисы на M1: `api`, `worker` (тот же образ), `postgres` (PostGIS) и одноразовый
`migrate` (миграции БД и bootstrap admin-токен из `BOOTSTRAP_ADMIN_TOKEN`,
в dev — `dev-admin-token`). Данные — в volume `data`. S3 и Redis добавятся на M2.

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