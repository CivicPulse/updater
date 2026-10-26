"""Preserve source observation ordering across offline replay."""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    op.execute("ALTER TABLE records ADD COLUMN observed_at TEXT NOT NULL DEFAULT ''")
    op.execute("UPDATE records SET observed_at=last_seen")


def downgrade():
    raise RuntimeError("State downgrade is unsupported; restore a compatible backup")
