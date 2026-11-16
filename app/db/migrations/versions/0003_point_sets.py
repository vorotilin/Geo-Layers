"""M2: наборы точек (design §3, §6). Снимок версии — .npz (M2 без pyarrow).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TABLE point_set (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace_id text NOT NULL REFERENCES namespace(id),
    title        text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE point_set_version (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    set_id       uuid NOT NULL REFERENCES point_set(id) ON DELETE CASCADE,
    version_no   integer NOT NULL CHECK (version_no >= 1),
    count        integer NOT NULL CHECK (count >= 0),
    snapshot_key text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (set_id, version_no)
);
"""

DOWNGRADE = """
DROP TABLE point_set_version;
DROP TABLE point_set;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
