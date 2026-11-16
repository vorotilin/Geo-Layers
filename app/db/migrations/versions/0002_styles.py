"""M2: стили слоёв (design §3, §7). Смена стиля = новая ревизия, ревизия входит
в URL тайла — кэш не сбрасывается (ADR-0005).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TABLE style (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace_id text NOT NULL REFERENCES namespace(id),
    name         text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9_-]{0,62}$'),
    spec         jsonb NOT NULL DEFAULT '{}',
    revision     integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (namespace_id, name, revision)
);
CREATE INDEX style_latest_idx ON style (namespace_id, name, revision DESC);
"""

DOWNGRADE = "DROP TABLE style;"


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
