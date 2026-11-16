# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).

## [Unreleased]

### Добавлено
- M2: предсборка пирамиды обзоров при публикации (design §5.3): публикация ставит
  warm-задание на обзорные зумы (0..z_s−1) в обеих схемах (3857 и 3395).
- M2: тепловая карта с радиусом в пикселях (`app/tiles/heatmap.py`, brief §3):
  `GET /v1/point-sets/{id}/heatmap/{tms}/{z}/{x}/{y}.png?radius=…` — чисто визуальная.
- M2: S3-хранилище (`app/storage.py::S3Storage`, boto3): источник в S3 (MinIO локально),
  локальное зеркало в CACHE_DIR для mmap; MinIO в compose + создание бакета в migrate.
- M2: табличный импорт сеток CSV/XLSX (`app/importer/tabular.py`, brief §1): геометрия
  WKT или «центр+размер»; диспетчеризация в задаче импорта по типу геометрии.
- M2: нормировка `per_layer` («на значение другого слоя»), фильтр по категории,
  rate limit на подписанных тайлах (ключ — префикс слоя).
- M2: сетка `polygons` и картограмма (brief §2–3, design §4–6): `app/grids/polygons.py`
  (lookup shapely + растеризация rasterio), `POST /v1/polygons` (импорт GeoJSON полигонов
  → сетка + слой), тайлы/значение для полигонов, `method="choropleth"` в
  `POST /v1/layers/build` (число/сумма + нормировки none/per_km2/share).
- M2: H3-бининг (`app/derive/h3bin.py`, brief §3): `method="h3"` в build API; гексагоны
  ложатся на общий механизм сетки `polygons`.
- Зависимости: `boto3`, `rasterio`, `h3`, `openpyxl`, `pyogrio` (для S3, тайлов полигонов,
  H3, XLSX, GeoPackage).
- M2: веб-интерфейс (brief §10): импорт наборов точек и построение слоя (bin/kde) —
  `ui/src/components/PointsForm.vue`; легенда выбранной версии в каталоге (`LayerPanel.vue`).
- M2: rate limit на дорогих ручках (`batch`/`aggregate`, brief §9): token bucket в памяти
  (`app/ratelimit.py`), ключ — токен, 429 с `Retry-After`; настройка
  `RATE_LIMIT_EXPENSIVE_RPS` (0 — выключен). Для нескольких процессов нужен Redis (known-issues).
- M2: прогрев и сброс кэша тайлов (brief §11): `POST /v1/cache/warm` (задание `warm_cache`
  по bbox и диапазону зумов) и `POST /v1/cache/purge` (диск + in-memory кэши).
- M2: картограмма (`app/derive/choropleth.py`, brief §3): пространственное соединение
  точек с полигонами (shapely STRtree), нормировки none/per_km2/share, геодезические
  площади. Данные и рейтинг считаются; рендер тайлов полигонов требует rasterio (known-issues).
- M2: callback'и в платформу (common-requirements §2–3, design §9): outbox `callback_delivery`
  (миграция 0004), подпись `X-Apc-Signature` = HMAC("{timestamp}.{raw_body}"), повторы
  10 с / 30 с / 2 мин / 10 мин / 1 ч / 6 ч; `callback_url` в запросе импорта; доставка
  из цикла воркера.
- M2: данные слоёв (`app/api/data.py`, brief §7): `POST /v1/values/batch` (пакет точек),
  `POST /v1/aggregate` (bbox или GeoJSON-полигон → mean/sum/min/max/cells/coverage),
  `GET /v1/layers/{id}/rating` (топ ячеек по значению). Работают на любой rectilinear-сетке.
- M2: KDE (тепловая карта) точек (`app/derive/kde.py`, brief §3): гауссово ядро с полосой
  в метрах, свёртка через FFT на numpy (без scipy), нормировка «точки на км²»; метод
  `kde` в `POST /v1/layers/build` (value_kind=intensive).
- M2: построение слоя из точек (`POST /v1/layers/build`, brief §3): метод `bin` —
  биннинг набора точек в квадратную сетку (UTM), результат — обычный draft-слой,
  дальше публикация/тайлы/значения общим механизмом (value_kind из агрегата).
- M2: наборы точек (brief §3, §8): таблицы `point_set`/`point_set_version` (миграция 0003),
  снимок версии в `.npz`; ручки `POST /v1/point-sets` (создание с первой версией),
  `POST …/{id}/versions` (пополнение), `GET /v1/point-sets` (список).
- M2: парсер наборов точек (`app/importer/points.py`, brief §3): CSV (автоопределение
  разделителя и колонок координат), GeoJSON FeatureCollection и NDJSON; координаты
  обязательны, вес/категория по желанию (вес по умолчанию 1), битые строки пропускаются
  со счётчиком ошибок.
- M2: биннинг точек в квадратную сетку (`app/derive/binning.py`, brief §3): проекция
  в UTM (честные метры), агрегаты count/sum/mean, пустая ячейка mean = «нет данных»;
  результат — обычная rectilinear-сетка для общего рендера.
- M2: обзоры на мелких зумах (design §5.3, brief §6): `app/tiles/overview.py` — площадь
  ячейки (сфера равной площади), агрегат `overview_tile` (среднее по площади для intensive,
  плотность на км² для extensive), даунсэмплинг 2×2 с пропуском NaN. Рендер на зумах ниже
  порога (ячейка < ~2 px) берёт обзорный агрегат с кэшем; `value_kind` задаёт тип агрегата.
  WGS84-прямоугольные сетки; прочие рисуются по центру пикселя (known-issues).
- M2: управление токенами `/v1/tokens` (common-requirements §3, brief §9): выпуск
  (секрет показывается один раз), список (только метаданные), отзыв без перезапуска;
  токен видит только токены своего namespace.
- M2: стили слоёв (design §7, brief §4–5): таблица `style` (миграция 0002), ручки
  `POST/GET/PUT /v1/styles` и `GET /v1/layers/{id}/legend`. Классификация
  equal/quantile/log/manual/jenks (чистая реализация Fisher/Jenks по перцентилям),
  именованные палитры, непрозрачность и цвет «нет данных», ревизии `name@rev` входят
  в URL тайла — смена стиля без сброса кэша. Тайлы рендерятся по стилю, легенда —
  из тех же breaks + palette, поэтому не расходятся с картинкой.
- M1, шаг 11. Веб-интерфейс `/ui` (Vue 3 + Vite + MapLibre, ADR-0010): вход по токену,
  каталог слоёв и версий, публикация, карта с подписанными тайлами и значением по клику,
  загрузка GeoJSON с манифестом и отчётом об ошибках. Стили минимальные — до стиля
  от дизайнеров (`ui/src/style.css`). Сборка — стадия Node в Dockerfile.
- Первый замер тайлов k6 (`bench/`, `docs/benchmarks.md`): 200 rps на 2 vCPU с p95 ≤ 8 мс
  для холодных тайлов; потолок ~300 rps холодных и ~700 rps из кэша.
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
- `shapely` перенесён в основные зависимости (используется в проде: агрегат по полигону,
  картограмма); ранее был только в dev — образ не поднимался.
- Healthcheck postgres в compose и CI — по TCP: при первой инициализации образ
  postgis перезапускается, и проверка по сокету давала ложное «готов».
- Valkey заменён на Redis 8; Redis используется только для rate limit, кэш тайлов — на диске.
- Проекции 3857/3395 берутся из morecantile вместо собственных формул (ADR-0006).
- Запасной план по скорости рендера: оптимизация numpy, затем сервис на Go (вместо Rust).
- Статус задания `cancelled`; Swagger UI на `/docs` только при `APP_ENV=dev`.
- `README.Md` переименован в `README.md`.
