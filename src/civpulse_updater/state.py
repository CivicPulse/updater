"""Updater-owned state. No connection to the Civic Pulse database."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from .models import Envelope, canonical, digest


def now() -> str:
    return datetime.now(UTC).isoformat()


class State:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{root.resolve() / 'state.sqlite3'}")
        cfg = Config()
        cfg.set_main_option(
            "script_location", str(Path(__file__).parent / "migrations")
        )
        with self.engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")

    def rows(self, sql: str, **params):
        with self.engine.connect() as connection:
            return [
                dict(row) for row in connection.execute(text(sql), params).mappings()
            ]

    def start_run(self, source: str, config: str, window: str, replay_of=None) -> str:
        run_id = str(uuid4())
        with self.engine.begin() as connection:
            # Caller holds the global process lock: old running rows are crashed runs.
            connection.execute(
                text("UPDATE runs SET status='interrupted' WHERE status='running'")
            )
            connection.execute(
                text("""INSERT INTO runs
                (id,source,started_at,status,config,window,replay_of,inputs,errors)
                VALUES (:id,:source,:at,'running',:config,:window,:replay,'[]','[]')"""),
                dict(
                    id=run_id,
                    source=source,
                    at=now(),
                    config=config,
                    window=window,
                    replay=replay_of,
                ),
            )
        return run_id

    def save_inputs(self, run_id: str, inputs: list, errors: list):
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE runs SET inputs=:inputs, errors=:errors WHERE id=:id"),
                dict(id=run_id, inputs=canonical(inputs), errors=canonical(errors)),
            )

    def observe(self, run_id: str, evidence: dict):
        with self.engine.begin() as connection:
            connection.execute(
                text("INSERT INTO evidence (run_id,metadata) VALUES (:id,:metadata)"),
                dict(id=run_id, metadata=canonical(evidence)),
            )

    def finish(self, run_id: str, envelopes: list[Envelope], errors: list) -> dict:
        proposed = 0
        skipped_stale = 0
        with self.engine.begin() as connection:
            origin = (
                connection.execute(
                    text("SELECT * FROM runs WHERE id=:id"), {"id": run_id}
                )
                .mappings()
                .one()
            )
            while origin["replay_of"]:
                origin = (
                    connection.execute(
                        text("SELECT * FROM runs WHERE id=:id"),
                        {"id": origin["replay_of"]},
                    )
                    .mappings()
                    .one()
                )
            observed_at = origin["started_at"]
            for envelope in envelopes:
                event = envelope.event
                normalized = event.model_dump(mode="json")
                if not event.documents:
                    normalized.pop("documents")  # Preserve pre-document event hashes.
                payload = canonical(normalized)
                sha = digest(payload.encode())
                key = dict(source=event.source_id, external=event.external_id)
                previous = (
                    connection.execute(
                        text(
                            "SELECT * FROM records WHERE source=:source AND external_id=:external"
                        ),
                        key,
                    )
                    .mappings()
                    .first()
                )
                if previous and previous["observed_at"] > observed_at:
                    skipped_stale += 1
                    continue
                if previous and previous["hash"] == sha:
                    connection.execute(
                        text(
                            "UPDATE records SET last_seen=:at,observed_at=:observed WHERE source=:source AND external_id=:external"
                        ),
                        {**key, "at": now(), "observed": observed_at},
                    )
                    continue
                proposed += 1
                before = json.loads(previous["payload"]) if previous else None
                connection.execute(
                    text("""UPDATE changes SET status='superseded'
                    WHERE source=:source AND external_id=:external AND status IN ('pending','approved')"""),
                    key,
                )
                connection.execute(
                    text("""INSERT INTO changes
                    (id,run_id,source,external_id,payload,hash,before_payload,evidence,status,created_at)
                    VALUES (:id,:run,:source,:external,:payload,:hash,:before,:evidence,'pending',:at)"""),
                    {
                        **key,
                        "id": str(uuid4()),
                        "run": run_id,
                        "payload": payload,
                        "hash": sha,
                        "before": canonical(before) if before else None,
                        "evidence": envelope.model_dump_json(),
                        "at": now(),
                    },
                )
                connection.execute(
                    text("""INSERT INTO records (source,external_id,payload,hash,last_seen,observed_at)
                    VALUES (:source,:external,:payload,:hash,:at,:observed)
                    ON CONFLICT(source,external_id) DO UPDATE SET
                    payload=excluded.payload, hash=excluded.hash,last_seen=excluded.last_seen,observed_at=excluded.observed_at"""),
                    {
                        **key,
                        "payload": payload,
                        "hash": sha,
                        "at": now(),
                        "observed": observed_at,
                    },
                )
            status = (
                "partial"
                if errors and envelopes
                else "failed"
                if errors
                else "complete"
            )
            connection.execute(
                text("""UPDATE runs SET status=:status,finished_at=:at,
                errors=:errors,record_count=:count,change_count=:changes WHERE id=:id"""),
                dict(
                    id=run_id,
                    status=status,
                    at=now(),
                    errors=canonical(errors),
                    count=len(envelopes),
                    changes=proposed,
                ),
            )
        return {
            "run_id": run_id,
            "status": status,
            "records": len(envelopes),
            "proposed_changes": proposed,
            "skipped_stale": skipped_stale,
            "errors": errors,
            "mode": "review-only",
        }

    def review(
        self, change_id: str, decision: str, actor: str, reason: str, acknowledge: bool
    ):
        if (
            decision not in ("approved", "rejected")
            or not actor.strip()
            or not reason.strip()
        ):
            raise ValueError("Review requires a decision, actor and reason")
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    text("SELECT * FROM changes WHERE id=:id"), {"id": change_id}
                )
                .mappings()
                .first()
            )
            if not row or row["status"] != "pending":
                raise ValueError("Change is missing or no longer pending")
            if digest(row["payload"].encode()) != row["hash"]:
                raise ValueError("Change checksum mismatch")
            payload = json.loads(row["payload"])
            if decision == "approved" and payload.get("warnings") and not acknowledge:
                raise ValueError("Warnings require explicit acknowledgement")
            connection.execute(
                text("UPDATE changes SET status=:status WHERE id=:id"),
                {"id": change_id, "status": decision},
            )
            connection.execute(
                text("""INSERT INTO decisions (change_id,decision,actor,reason,created_at)
                VALUES (:id,:decision,:actor,:reason,:at)"""),
                dict(
                    id=change_id,
                    decision=decision,
                    actor=actor,
                    reason=reason,
                    at=now(),
                ),
            )
        return {"change_id": change_id, "status": decision, "published": False}
