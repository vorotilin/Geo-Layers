"""M1: начальная схема — токены, файлы, задания, импорт, сетки, слои и версии.

Урезанная схема design §3 (docs/plan-m1.md, шаг 4). Точки, производные слои,
стили и callback'и — отдельными миграциями на M2.

Revision ID: 0001
Revises:
Create Date: 2026-10-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE EXTENSION IF NOT EXISTS postgis;

-- Граница приватности. На M1 только служебный `shared` (данные поставщиков).
CREATE TABLE namespace (
    id          text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_-]{0,62}$'),
    title       text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO namespace (id, title) VALUES ('shared', 'Данные поставщиков');

-- Токены API хранятся хэшем sha256; отзыв — revoked_at, без перезапуска.
CREATE TABLE token (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    hash          bytea NOT NULL UNIQUE CHECK (length(hash) = 32),
    title         text NOT NULL,
    scopes        text[] NOT NULL CHECK (scopes <@ ARRAY['admin', 'write', 'read']),
    namespace_id  text NOT NULL REFERENCES namespace(id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    revoked_at    timestamptz
);

-- Исходный файл, хранится как есть (M1 — DATA_DIR, с M2 — S3).
CREATE TABLE file (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace_id  text NOT NULL REFERENCES namespace(id),
    sha256        bytea NOT NULL CHECK (length(sha256) = 32),
    size          bigint NOT NULL CHECK (size >= 0),
    filename      text,
    format        text,
    storage_key   text NOT NULL,
    token_id      uuid REFERENCES token(id),
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX file_sha256_idx ON file (namespace_id, sha256);

-- Очередь заданий (ADR-0003): захват SELECT … FOR UPDATE SKIP LOCKED,
-- зависшие подбираются по heartbeat_at.
CREATE TABLE job (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type             text NOT NULL,
    status           text NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    progress         real NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    attempts         integer NOT NULL DEFAULT 0,
    max_attempts     integer NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    namespace_id     text NOT NULL REFERENCES namespace(id),
    token_id         uuid REFERENCES token(id),
    idempotency_key  text,
    payload          jsonb NOT NULL DEFAULT '{}',
    result           jsonb,
    error            jsonb,
    run_after        timestamptz NOT NULL DEFAULT now(),
    locked_by        text,
    heartbeat_at     timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    started_at       timestamptz,
    finished_at      timestamptz,
    updated_at       timestamptz NOT NULL DEFAULT now()
);
-- Очередь к выдаче: только queued, по времени готовности.
CREATE INDEX job_queue_idx ON job (run_after, created_at) WHERE status = 'queued';
-- Подбор зависших.
CREATE INDEX job_running_idx ON job (heartbeat_at) WHERE status = 'running';
CREATE UNIQUE INDEX job_idempotency_idx ON job (token_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Импорт файла по манифесту. Дедупликация: sha256 файла + хэш манифеста (ADR-0009).
CREATE TABLE import (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace_id   text NOT NULL REFERENCES namespace(id),
    file_id        uuid NOT NULL REFERENCES file(id),
    file_sha256    bytea NOT NULL,
    manifest       jsonb NOT NULL,
    manifest_hash  bytea NOT NULL,
    job_id         uuid REFERENCES job(id),
    report         jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (namespace_id, file_sha256, manifest_hash)
);

-- Набор = логическая группа слоёв одного поставщика.
CREATE TABLE dataset (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace_id  text NOT NULL REFERENCES namespace(id),
    provider      text NOT NULL,
    title         text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (namespace_id, provider, title)
);

-- Сетка: ячейки с устойчивыми номерами 0..N-1 (ADR-0004).
-- params для rectilinear: отсортированные границы столбцов и строк в исходной CRS.
CREATE TABLE grid (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace_id  text NOT NULL REFERENCES namespace(id),
    provider      text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('rectilinear', 'h3', 'quadkey', 'polygons')),
    crs           text NOT NULL,
    params        jsonb NOT NULL DEFAULT '{}',
    cell_count    integer NOT NULL DEFAULT 0 CHECK (cell_count >= 0),
    fingerprint   text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX grid_provider_idx ON grid (namespace_id, provider);

-- Ячейки только добавляются: idx растёт, старые массивы читают хвост как NaN.
CREATE TABLE grid_cell (
    grid_id  uuid NOT NULL REFERENCES grid(id) ON DELETE CASCADE,
    idx      integer NOT NULL CHECK (idx >= 0),
    key      text NOT NULL,
    geom     geometry(Geometry, 4326) NOT NULL,
    area_m2  double precision,
    attrs    jsonb,
    PRIMARY KEY (grid_id, idx),
    UNIQUE (grid_id, key)
);
CREATE INDEX grid_cell_geom_idx ON grid_cell USING gist (geom);

-- Слой: одна метрика набора. value_kind задаёт агрегацию.
CREATE TABLE layer (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id  uuid NOT NULL REFERENCES dataset(id),
    slug        text NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9_]{0,62}$'),
    title       text NOT NULL,
    unit        text,
    value_kind  text NOT NULL CHECK (value_kind IN ('intensive', 'extensive')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, slug)
);

-- Версия слоя: (layer, period, revision); значения — файл float32[N] по values_key.
-- Текущая для периода = последняя опубликованная.
CREATE TABLE layer_version (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    layer_id      uuid NOT NULL REFERENCES layer(id),
    period        text NOT NULL,
    revision      integer NOT NULL CHECK (revision >= 1),
    grid_id       uuid NOT NULL REFERENCES grid(id),
    status        text NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft', 'published', 'archived')),
    values_key    text NOT NULL,
    stats         jsonb,
    import_id     uuid REFERENCES import(id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    published_at  timestamptz,
    UNIQUE (layer_id, period, revision)
);
CREATE INDEX layer_version_current_idx ON layer_version (layer_id, period, published_at DESC)
    WHERE status = 'published';
"""

DOWNGRADE = """
DROP TABLE layer_version;
DROP TABLE layer;
DROP TABLE grid_cell;
DROP TABLE grid;
DROP TABLE dataset;
DROP TABLE import;
DROP TABLE job;
DROP TABLE file;
DROP TABLE token;
DROP TABLE namespace;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
