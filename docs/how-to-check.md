# Как запустить и проверить M1

Проверка занимает около 15 минут. Одна команда поднимает сервис, один скрипт
проверяет весь сценарий M1 целиком, дальше — проверка руками в браузере и через API.
Все команды — для PowerShell, из папки проекта `C:\Users\User\Desktop\pd_geo_sloi`.

## 0. Что понадобится

| Что | Зачем | Как проверить |
|---|---|---|
| Docker Desktop, запущенный | api, worker, PostGIS | `docker version` показывает блок `Server` |
| Python 3.10+ | скрипт проверки `scripts\e2e.py` (только стандартная библиотека) | `py --version` |
| `data\layers_sample_2023Q1.zip` | проверка на настоящем образце | уже лежит в `data\`, в git не попадает |
| Свободный порт 8000 | адрес сервиса `http://localhost:8000` | см. ниже |

Если запущен мой проверочный стек `apc-check`, он занимает порт 8000. Остановите его:

```powershell
docker compose -p apc-check down -v
```

Локальный PostgreSQL 17 на порту 5432 не мешает: база сервиса живёт внутри Docker
и наружу порт не открывает.

## 1. Запустить сервис

```powershell
cd C:\Users\User\Desktop\pd_geo_sloi
docker compose up -d --build --wait
```

Первая сборка идёт 3–5 минут: скачиваются образы, пакеты Python и Node. Повторные
сборки занимают секунды. Команда завершается, когда все сервисы здоровы.

```powershell
docker compose ps
curl.exe http://localhost:8000/health
curl.exe http://localhost:8000/ready
```

В PowerShell нужно писать именно `curl.exe`: просто `curl` там — другая команда.

Что должно быть:

- `docker compose ps` — `api`, `worker`, `postgres` в состоянии `Up (healthy)`.
  `migrate` отработал и вышел, поэтому его в списке нет — так и должно быть.
- `/health` → `{"status":"ok","version":"dev"}`.
- `/ready` → `{"status":"ready","checks":{"database":"ok","storage":"ok"}}`.

Файл `.env` не нужен: у всех настроек есть значения для разработки.
Токен администратора — `dev-admin-token`, он понадобится дальше.

## 2. Автоматическая проверка всего сценария

```powershell
py scripts\e2e.py --sample data\layers_sample_2023Q1.zip
```

Скрипт проходит весь сценарий M1 и сверяет ответы с заранее известными. В конце он
печатает `OK`; при любой ошибке — `ОШИБКА: …` и останавливается. Без `--sample`
проверяется только синтетика — так скрипт запускается в CI.

Что он делает:

1. Строит синтетическую сетку из 2 215 квадратов с дырами. Значение ячейки =
   номер строки · 1000 + номер столбца, поэтому правильный ответ известен заранее.
2. Загружает файл, запускает импорт и ждёт, пока воркер выполнит задание.
3. Публикует версию и сверяет значение в 58 точках с формулой. Точка вне сетки
   должна дать `null`.
4. Подписывает ссылку и скачивает тайл PNG в EPSG:3857 и EPSG:3395. Тот же тайл
   без подписи должен дать 403.
5. С `--sample` загружает три файла образца. Проверяет, что они легли на одну сетку
   и уложились в 2 минуты, и сверяет значение первой ячейки дохода с файлом.

Так выглядел мой прогон на чистой сборке (миллисекунды и размеры у вас будут другие):

```text
Синтетика: квадраты CRS84, значение = row·1000 + col
  значений сверено: 58
  тайл 3857 z12: 433 байт, холодный 44 мс, из кэша 18 мс
  тайл 3395 z12: 279 байт, холодный 19 мс, из кэша 4 мс
  mean_income_home_2023Q1.geojson: 108977 ячеек, ошибок 0, 4.0 с, сетка 108977
  realty_commercial_real_estate_2023Q1.geojson: 88364 ячеек, ошибок 0, 2.4 с, сетка 121281
  cnt_job_2023Q1.geojson: 78440 ячеек, ошибок 0, 2.3 с, сетка 128132
Образец целиком: 9.3 с (требование ≤ 120 с)
  тайл 3857 z12: 342 байт, холодный 15 мс, из кэша 9 мс
  тайл 3395 z12: 695 байт, холодный 15 мс, из кэша 29 мс
OK
```

Главное: итоговая сетка — 128 132 ячейки, ошибок 0, последняя строка — `OK`.
Повторный запуск с образцом пройдёт быстрее. Тот же файл с тем же манифестом второй раз
не импортируется (дедупликация): сервис просто вернёт прошлый результат.

Если вместо русских букв видны кракозябры, перед запуском выполните
`$env:PYTHONIOENCODING = "utf-8"`.

## 3. Проверка через веб-интерфейс

Откройте `http://localhost:8000/ui/` и войдите с токеном `dev-admin-token`.
Стили интерфейса минимальные специально: стиль придёт от дизайнеров.

После шага 2 в списке «Слои» есть четыре слоя: «E2E синтетика», «Средний доход»,
«Доля коммерческой недвижимости», «Рабочие места».

**Карта и значения:**

1. Нажмите «Средний доход». В таблице версий должна быть одна строка: `2023Q1`,
   `published (текущая)`, 108 977 ячеек. Под таблицей — мин 10 000, сред ≈ 42 811,
   макс 107 500.
2. На карте поверх OpenStreetMap появится Москва и область в цвете: от жёлтого к
   тёмно-красному, к центру — темнее. Ячейки должны точно совпадать с подложкой,
   без сдвига. Пустые места — ячейки без данных, это «неизвестно», а не ноль.
3. Кликните по центру Москвы — появится подсказка «Средний доход: 45 869» (число
   зависит от места клика). Клик за пределами данных покажет «—».
4. Ползунок «Прозрачность слоя» меняет прозрачность слоя.

**Загрузка новой версии через форму:**

1. Распакуйте образец: `Expand-Archive data\layers_sample_2023Q1.zip -DestinationPath data\sample`
   (папка `data\sample` в git не попадает).
2. В форме «Загрузить сетку» выберите файл `data\sample\cnt_job_2023Q1.geojson` и заполните:
   - Поставщик — `sample`;
   - Набор данных — `Рабочие места`;
   - Период — `2023Q2`;
   - slug — `cnt_job` (подставится из имени файла);
   - Агрегация — «сумма (счётчик)».

   Поставщик, набор и slug нужны именно такие, чтобы версия попала в уже существующий
   слой «Рабочие места».
3. Нажмите «Импортировать». Статус задания пройдёт `queued → running → succeeded`.
   В отчёте будет: объектов 78 440, принято 78 440, отброшено 0, сетка 128 132 ячейки (+0).
   «+0» значит, что новые ячейки не понадобились: файл лёг на существующую сетку.
4. Откройте слой «Рабочие места». В нём появилась версия `2023Q2` со статусом `draft`.
   Нажмите «Опубликовать» — она станет текущей.

**Ошибочный файл:** загрузите через форму любой не-JSON файл, переименовав его в `.geojson`.
Задание завершится со статусом `failed` и сообщением «Файл не читается как JSON».

## 4. Проверка API вручную

Каждый шаг можно повторить руками в PowerShell. Все команды ниже я проверил на работающем сервисе.

```powershell
$B = "http://localhost:8000"
$H = @{ Authorization = "Bearer dev-admin-token" }

# Без токена — 401 в едином формате ошибок
curl.exe -s "$B/v1/layers"

# 1. Загрузка файла → id
$f = curl.exe -s -H "Authorization: Bearer dev-admin-token" `
  -F "file=@data\sample\cnt_job_2023Q1.geojson" "$B/v1/files" | ConvertFrom-Json
$f

# 2. Импорт по манифесту → 202, job_id и status_url
$body = @{
  file_id  = $f.id
  manifest = @{
    kind = "grid"; period = "2023Q1"; key = "zone_id"
    dataset  = @{ provider = "manual"; title = "Ручная проверка" }
    geometry = @{ type = "geojson" }
    metrics  = @(@{ column = "val"; slug = "cnt_job"; title = "Рабочие места";
                    unit = "шт."; value_kind = "extensive" })
  }
} | ConvertTo-Json -Depth 5
$imp = Invoke-RestMethod -Method Post "$B/v1/imports" -Headers $H `
  -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
$imp

# 3. Статус задания (через несколько секунд — succeeded) и созданная версия-черновик
$job = Invoke-RestMethod "$B/v1/jobs/$($imp.job_id)" -Headers $H
$job.status
$v = $job.result.versions[0]
$v

# Отчёт импорта: ошибки по строкам, сетка
(Invoke-RestMethod "$B/v1/imports/$($imp.import_id)" -Headers $H).report | ConvertTo-Json -Depth 5

# 4. Публикация
Invoke-RestMethod -Method Post "$B/v1/layers/$($v.layer_id)/versions/$($v.version_id)/publish" -Headers $H

# 5. Значение в точке (центр Москвы)
Invoke-RestMethod "$B/v1/values?lat=55.75&lng=37.62&layers=$($v.layer_id)" -Headers $H |
  ConvertTo-Json -Depth 5

# 6. Подписанная ссылка на тайлы и сам тайл (tms: 3857 или 3395)
$s = Invoke-RestMethod -Method Post "$B/v1/tiles/sign" -Headers $H -ContentType "application/json" `
  -Body (@{ layer = $v.layer_id; tms = "3857" } | ConvertTo-Json)
$url = $s.url_template.Replace("{z}", "10").Replace("{x}", "619").Replace("{y}", "320")
curl.exe -s -o "$env:TEMP\tile.png" -w "%{http_code} %{content_type}`n" $url   # 200 image/png
curl.exe -s -o NUL -w "%{http_code}`n" $url.Split("?")[0]                    # 403 без подписи
start "$env:TEMP\tile.png"
```

Ожидаемое: на шаге 3 у версии `count` = 78440, на шаге 5 — `value` = 604.0
(рабочих мест в ячейке центра Москвы), тайл — 200, без подписи — 403.

Если повторить шаг 2 с тем же файлом и манифестом, сервис вернёт 200 и тот же `job_id` —
это дедупликация импорта.

Windows PowerShell 5.1 показывает русские буквы из ответов кракозябрами (`ÑÑ.` вместо
`шт.`). Данные при этом верные — так PowerShell 5.1 декодирует ответ; в `curl.exe`,
браузере и UI текст нормальный.

Полный контракт API — `openapi.yaml` в корне. Swagger UI — `http://localhost:8000/docs`.

## 5. Тесты разработчика

Линтер, проверка типов и 80 тестов. 49 из них нужна PostGIS — поднимаем её
отдельным контейнером на порту 55432.

```powershell
py -m pip install --user uv==0.13.0
py -m uv sync

docker run -d --name apc-test-pg -p 55432:5432 -e POSTGRES_DB=apc_test `
  -e POSTGRES_USER=apc -e POSTGRES_PASSWORD=apc postgis/postgis:16-3.4
# подождать ~15 секунд: при первом старте образ PostGIS перезапускается

$env:TEST_DATABASE_URL = "postgresql://apc:apc@127.0.0.1:55432/apc_test"
py -m uv run ruff check .
py -m uv run mypy app tests
py -m uv run pytest
```

Ожидаемое: `All checks passed!`, `Success: no issues found`, `80 passed`. Без
`TEST_DATABASE_URL` тесты с БД пропускаются (`skipped`), а не падают. Если контейнер
`apc-test-pg` уже есть, вместо `docker run` выполните `docker start apc-test-pg`.

Внимание: тесты пересоздают схему `public` в этой базе. Не указывайте в
`TEST_DATABASE_URL` рабочую базу.

## 6. Замер скорости тайлов (необязательно)

Профиль «маленький сервер» — 2 vCPU и 4 ГБ на сервис. Нужны данные образца (шаг 2).

```powershell
docker compose -f docker-compose.yml -f bench/compose.bench.yml up -d --wait
$L = docker compose exec -T postgres psql -U apc -d apc -tAc `
  "select id from layer where slug='mean_income_home'"
Get-Content bench\tiles.js | docker run --rm -i --network pd_geo_sloi_default `
  -e BASE=http://api:8000 -e LAYER=$L -e MODE=cold -e RATE=200 grafana/k6 run -
```

Ожидаемое: `http_reqs` ≈ 200/s, `http_req_failed` 0 %, p95 `http_req_duration` —
единицы миллисекунд. `MODE=warm` проверяет тайлы из кэша. Первый прогон сразу после
старта медленнее: процессы прогреваются. Мои результаты и выводы —
`docs/benchmarks.md`.

## 7. Остановка и сброс

```powershell
docker compose down        # остановить, данные сохранятся
docker compose down -v     # остановить и удалить все данные (база, файлы, кэш тайлов)
docker rm -f apc-test-pg   # тестовая база из шага 5
```

## Если что-то не работает

| Симптом | Что делать |
|---|---|
| `failed to connect to the docker API … dockerDesktopLinuxEngine` | Не запущен Docker Desktop — запустите и подождите, пока он поднимется |
| `docker compose up` не может скачать образы | Сеть до Docker Hub: в Docker Desktop → Settings → Docker Engine добавить `"registry-mirrors": ["https://mirror.gcr.io"]`, либо включить VPN |
| `port is already allocated` на 8000 | Порт занят: `docker compose -p apc-check down -v` или другой процесс на 8000 |
| `/ready` отвечает 503 | Смотреть `docker compose logs migrate postgres` |
| Импорт `failed` | `GET /v1/jobs/{id}` — поле `error`; `GET /v1/imports/{id}` — отчёт по строкам; логи — `docker compose logs worker` |
| Кракозябры в выводе `e2e.py` | `$env:PYTHONIOENCODING = "utf-8"` |
| Тайлы не видны в UI | Слой без опубликованной версии: выберите версию в таблице или опубликуйте черновик |

Логи всех сервисов: `docker compose logs -f api worker` (JSON, по строке на событие).
