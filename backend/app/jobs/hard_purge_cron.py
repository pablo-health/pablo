# Copyright (c) 2026 Pablo Health, LLC. All rights reserved under AGPL-3.0.

"""Soft-delete hard purge job (THERAPY-cgy), stage 2 of the retention model.

When ``COMPLIANCE_HARD_PURGE_ENABLED`` is false — the Pablo Core default —
this module exits before opening a database connection so self-hosted CEs
never run hosted-only physical purge logic.

When true (hosted jobs: set on the Cloud Run Job), physically deletes
clinical rows for patients whose ``deleted_at`` is before the configurable
cutoff, writes the hosted compliance retention stub row, appends a
``patient_purged`` audit record, and deletes dependent rows. Cloud Storage
audio deletion is intentionally not implemented here yet.

Retention stub write: this is **not** a swappable override or ORM hook —
there is one implementation (``_insert_retention_stub``) that runs a
parameterized ``INSERT`` against the hosted ``compliance`` schema. SaaS
owns the table DDL; SQL identifiers still use legacy names
(``patient_identity_tombstone``, ``tombstoned_*``) even though product
language calls this a **minimal retention stub**.

Invoked as (from repo ``backend/`` with Poetry's default paths)::

    python -m app.jobs.hard_purge_cron --dry-run
    python -m app.jobs.hard_purge_cron --purge-before 2026-01-01T00:00:00Z

Cloud Run should set ``PYTHONPATH=/app/backend`` on the OSS image filesystem layout.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from ..db import PLATFORM_SCHEMA, _validate_schema_name, get_engine
from ..db.migrate_tenants import list_active_practice_registry
from ..models.audit import AUDIT_LOG_RETENTION_DAYS, AuditAction, ResourceType
from ..settings import get_settings

logger = logging.getLogger(__name__)

_RETENTION_JOB_USER_ID = "system:retention_job"


@dataclass(frozen=True, slots=True)
class _ComplianceRetentionStubPayload:
    patient_id: str
    display_name: str
    dob: str | None
    practice_id: str
    schema_name: str
    reason: str


def _compliance_schema_exists(conn: Any) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'compliance'"
        )
    ).fetchone()
    return row is not None


def _parse_purge_before(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(UTC) - timedelta(days=30)
    normalized = raw.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _purge_before_timestamp(purge_before: datetime) -> str:
    return purge_before.isoformat().replace("+00:00", "Z")


def _fetch_purgeable_patient_ids(engine: Any, schema: str, purge_before: datetime) -> list[str]:
    _validate_schema_name(schema)
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        rows = conn.execute(
            text(
                "SELECT id FROM patients "
                "WHERE deleted_at IS NOT NULL AND deleted_at < :cutoff "
                "ORDER BY id"
            ),
            {"cutoff": purge_before},
        ).fetchall()
    return [row[0] for row in rows]


def _patient_row_for_stub(
    conn: Any, schema: str, patient_id: str, purge_before: datetime
) -> dict[str, Any] | None:
    _validate_schema_name(schema)
    conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
    row = conn.execute(
        text(
            "SELECT id, first_name, last_name, date_of_birth "
            "FROM patients WHERE id = :pid AND deleted_at IS NOT NULL "
            "AND deleted_at < :cutoff FOR UPDATE"
        ),
        {"pid": patient_id, "cutoff": purge_before},
    ).mappings().first()
    return dict(row) if row else None


def _retention_stub_row_exists(conn: Any, patient_id: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM compliance.patient_identity_tombstone "
            "WHERE patient_id = :pid"
        ),
        {"pid": patient_id},
    ).fetchone()
    return row is not None


def _insert_retention_stub(conn: Any, stub: _ComplianceRetentionStubPayload) -> None:
    """Persist minimal identity retention metadata (legacy table name in SQL)."""
    expires = datetime.now(UTC) + timedelta(days=AUDIT_LOG_RETENTION_DAYS)
    base_cols = (
        "INSERT INTO compliance.patient_identity_tombstone ("
        " patient_id, name, dob, mrn, tenant_id_at_offboard, "
        " original_practice_schema, tombstoned_at, tombstoned_reason, expires_at"
        ") VALUES ("
    )
    tail = (
        " CAST(NULL AS text), :tenant, :schema, "
        " NOW(), :reason, :exp"
        ")"
    )
    params_base: dict[str, Any] = {
        "pid": stub.patient_id,
        "name": stub.display_name,
        "tenant": stub.practice_id,
        "schema": stub.schema_name,
        "reason": stub.reason,
        "exp": expires,
    }
    if stub.dob:
        sql = base_cols + " :pid, :name, CAST(:dob AS date)," + tail
        conn.execute(text(sql), {**params_base, "dob": stub.dob})
    else:
        sql = base_cols + " :pid, :name, NULL::date," + tail
        conn.execute(text(sql), params_base)


def _append_purge_audit(conn: Any, schema: str, patient_id: str) -> None:
    _validate_schema_name(schema)
    conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
    entry_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=AUDIT_LOG_RETENTION_DAYS)
    conn.execute(
        text(
            "INSERT INTO audit_logs ("
            " id, timestamp, expires_at, user_id, action, resource_type, "
            " resource_id, patient_id, changes"
            ") VALUES ("
            " :id, :ts, :exp, :uid, :action, :rtype, :rid, :pid, CAST(:changes AS jsonb)"
            ")"
        ),
        {
            "id": entry_id,
            "ts": now,
            "exp": expires_at,
            "uid": _RETENTION_JOB_USER_ID,
            "action": AuditAction.PATIENT_PURGED.value,
            "rtype": ResourceType.PATIENT.value,
            "rid": patient_id,
            "pid": patient_id,
            "changes": '{"source": "hard_purge_cron"}',
        },
    )


def _delete_clinical_rows(conn: Any, schema: str, patient_id: str) -> None:
    _validate_schema_name(schema)
    conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
    conn.execute(text("DELETE FROM appointments WHERE patient_id = :pid"), {"pid": patient_id})
    conn.execute(text("DELETE FROM notes WHERE patient_id = :pid"), {"pid": patient_id})
    conn.execute(text("DELETE FROM therapy_sessions WHERE patient_id = :pid"), {"pid": patient_id})
    conn.execute(
        text("DELETE FROM ical_client_mappings WHERE patient_id = :pid"), {"pid": patient_id}
    )
    conn.execute(text("DELETE FROM patients WHERE id = :pid"), {"pid": patient_id})


def run(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = _parse_argv(argv)

    settings = get_settings()
    if not settings.compliance_hard_purge_enabled:
        logger.info("hard_purge_disabled_by_policy")
        return 0

    engine = get_engine()
    purge_before = _parse_purge_before(args.purge_before_raw)

    with engine.connect() as conn:
        if not _compliance_schema_exists(conn):
            logger.error("compliance_schema_missing")
            return 2

    dry_run_marker = " dry_run=true" if args.dry_run else ""
    logger.info(
        "hard_purge_start purge_before=%s%s",
        _purge_before_timestamp(purge_before),
        dry_run_marker,
    )

    registry = list_active_practice_registry(engine)
    scanned = 0
    processed = 0

    for schema_name, practice_id in registry:
        _validate_schema_name(schema_name)
        patient_ids = _fetch_purgeable_patient_ids(engine, schema_name, purge_before)
        for patient_id in patient_ids:
            scanned += 1
            if args.dry_run:
                processed += 1
                continue
            try:
                with engine.begin() as conn:
                    row = _patient_row_for_stub(
                        conn, schema_name, patient_id, purge_before
                    )
                    if row is None:
                        continue
                    if not _retention_stub_row_exists(conn, patient_id):
                        name = (row["first_name"] + " " + row["last_name"]).strip() or "(unknown)"
                        dob_raw = row.get("date_of_birth") or None
                        if dob_raw == "":
                            dob_raw = None
                        _insert_retention_stub(
                            conn,
                            _ComplianceRetentionStubPayload(
                                patient_id=str(row["id"]),
                                display_name=name,
                                dob=str(dob_raw) if dob_raw else None,
                                practice_id=practice_id,
                                schema_name=schema_name,
                                reason="PATIENT_PURGED",
                            ),
                        )
                    _append_purge_audit(conn, schema_name, patient_id)
                    _delete_clinical_rows(conn, schema_name, patient_id)
                    processed += 1
            except Exception:
                logger.exception("hard_purge_patient_failed")
                raise

    logger.info(
        "hard_purge_done purgeable_candidates=%s rows_processed=%s dry_run=%s",
        scanned,
        processed if not args.dry_run else scanned,
        args.dry_run,
    )
    return 0


def _parse_argv(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--purge-before",
        dest="purge_before_raw",
        default=None,
        help=(
            "ISO-8601 cutoff (UTC). Rows with deleted_at strictly before this "
            "instant are eligible. Default: now minus 30 days."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count eligible patients without modifying the database.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
