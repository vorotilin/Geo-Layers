"""M2: callback'и в платформу — outbox с повторами (design §3, common-requirements §2).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
CREATE TABLE callback_delivery (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id       uuid NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    url          text NOT NULL,
    body         jsonb NOT NULL,
    attempt      integer NOT NULL DEFAULT 0,
    next_at      timestamptz NOT NULL DEFAULT now(),
    last_status  integer,
    delivered_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX callback_due_idx ON callback_delivery (next_at) WHERE delivered_at IS NULL;
"""

DOWNGRADE = "DROP TABLE callback_delivery;"


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
