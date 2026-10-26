"""Initial review-only state; publication tables intentionally absent."""

from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.execute("""CREATE TABLE runs (
        id TEXT PRIMARY KEY, source TEXT NOT NULL, started_at TEXT NOT NULL,
        finished_at TEXT, status TEXT NOT NULL, config TEXT NOT NULL,
        window TEXT NOT NULL, replay_of TEXT, inputs TEXT NOT NULL,
        errors TEXT NOT NULL, record_count INTEGER DEFAULT 0, change_count INTEGER DEFAULT 0
    )""")
    op.execute("""CREATE TABLE evidence (
        id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, metadata TEXT NOT NULL
    )""")
    op.execute("""CREATE TABLE records (
        source TEXT NOT NULL, external_id TEXT NOT NULL, payload TEXT NOT NULL,
        hash TEXT NOT NULL, last_seen TEXT NOT NULL, PRIMARY KEY(source, external_id)
    )""")
    op.execute("""CREATE TABLE changes (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source TEXT NOT NULL,
        external_id TEXT NOT NULL, payload TEXT NOT NULL, hash TEXT NOT NULL,
        before_payload TEXT, evidence TEXT NOT NULL, status TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    op.execute("""CREATE TABLE decisions (
        id INTEGER PRIMARY KEY, change_id TEXT NOT NULL, decision TEXT NOT NULL,
        actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
    )""")


def downgrade():
    raise RuntimeError("State downgrade is unsupported; restore a compatible backup")
