# Copyright (c) 2026 Pablo Health, LLC. All rights reserved under AGPL-3.0.

"""Soft-delete hard purge job (THERAPY-cgy), stage 2 of the retention model.

Requires a registered :class:`~app.jobs.hard_purge_retention_stub.ComplianceRetentionStubWriter`
(see ``get_compliance_retention_stub_writer``). Hosted images register
:class:`~app.jobs.hard_purge_retention_stub.SqlComplianceRetentionStubWriter`
from a deployment-specific entrypoint. Self-hosted invocations of
``python -m app.jobs.hard_purge_cron`` with **no** writer registered
exit **0** without opening a database connection — soft-delete plus
audit remains the only semantics.

Before any deletes, the writer's ``is_supported`` probes the DB; if False,
exit **2** (misconfigured deployment or DDL not applied).

Retention stub semantics live in ``hard_purge_retention_stub``. Cloud Storage
cleanup runs inside the per-patient transaction (THERAPY-zu4): blobs referenced
by ``therapy_sessions.audio_gcs_path`` **and** ``patient_documents.gcs_path``
are deleted **before** the clinical-row DELETE commits, so a GCS failure rolls
the whole purge back rather than leaving an audit row claiming a deletion that
didn't happen. The BAA §7 Stage 2 promise covers audio of soft-deleted patients
regardless of the per-practice audio retention slider.

The clinical-row delete removes every patient-associated surface: appointments,
notes, therapy_sessions, ical_client_mappings, chat history
(chat_conversations + chat_messages — which have no FK cascade from patients),
patient_documents (FK cascade), and the patients row itself. A hard purge
leaves zero rows and zero blobs for the patient across all surfaces.

Invoked from repo ``backend/``::

    python -m app.jobs.hard_purge_cron --dry-run
    python -m app.jobs.hard_purge_cron --purge-before 2026-01-01T00:00:00Z

Cloud Run (**hosted**) should use ``PYTHONPATH=/app/backend`` plus the
deployment-specific entrypoint that registers a retention-stub writer.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from ..db import PLATFORM_SCHEMA, _validate_schema_name, get_engine
from ..db.migrate_tenants import list_active_practice_registry
from ..models.audit import AUDIT_LOG_RETENTION_DAYS, AuditAction, ResourceType
from .hard_purge_retention_stub import (
    ComplianceRetentionStubPayload,
    get_compliance_retention_stub_writer,
)

logger = logging.getLogger(__name__)

_RETENTION_JOB_USER_ID = "system:retention_job"


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
        # Operator job: schema validated by _validate_schema_name(); not web-reachable.
        # nosemgrep
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
    # Operator job: schema validated by _validate_schema_name(); not web-reachable.
    # nosemgrep
    conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
    row = (
        conn.execute(
            text(
                "SELECT id, first_name, last_name, date_of_birth "
                "FROM patients WHERE id = :pid AND deleted_at IS NOT NULL "
                "AND deleted_at < :cutoff FOR UPDATE"
            ),
            {"pid": patient_id, "cutoff": purge_before},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _append_purge_audit(conn: Any, schema: str, patient_id: str) -> None:
    _validate_schema_name(schema)
    # Operator job: schema validated by _validate_schema_name(); not web-reachable.
    # nosemgrep
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
    # Operator job: schema validated by _validate_schema_name(); not web-reachable.
    # nosemgrep
    conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
    conn.execute(text("DELETE FROM appointments WHERE patient_id = :pid"), {"pid": patient_id})
    conn.execute(text("DELETE FROM notes WHERE patient_id = :pid"), {"pid": patient_id})
    conn.execute(text("DELETE FROM therapy_sessions WHERE patient_id = :pid"), {"pid": patient_id})
    conn.execute(
        text("DELETE FROM ical_client_mappings WHERE patient_id = :pid"), {"pid": patient_id}
    )
    # Chat history is patient-associated but chat_conversations.patient_id has no
    # FK to patients, so it is NOT removed by the patients-row cascade. Delete the
    # messages (defensively, even though chat_messages cascades from its parent
    # conversation) then the conversations, so a purged patient leaves no chat
    # rows behind.
    conn.execute(
        text(
            "DELETE FROM chat_messages WHERE conversation_id IN "
            "(SELECT id FROM chat_conversations WHERE patient_id = :pid)"
        ),
        {"pid": patient_id},
    )
    conn.execute(
        text("DELETE FROM chat_conversations WHERE patient_id = :pid"), {"pid": patient_id}
    )
    # patient_documents rows DO cascade from the patients delete below (FK
    # ON DELETE CASCADE); their GCS blobs are removed separately in run()
    # before this commit (see _delete_document_blobs).
    conn.execute(text("DELETE FROM patients WHERE id = :pid"), {"pid": patient_id})


def _audio_objects_for_patient(conn: Any, schema: str, patient_id: str) -> list[str]:
    """Return GCS object names referenced by the patient's sessions.

    ``therapy_sessions.audio_gcs_path`` may hold a single object name or a
    comma-separated pair (stereo: ``"<therapist>,<client>"`` — see
    ``app.routes.sessions``). Empty parts are dropped.
    """
    _validate_schema_name(schema)
    # Operator job: schema validated by _validate_schema_name(); not web-reachable.
    # nosemgrep
    conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
    rows = conn.execute(
        text(
            "SELECT audio_gcs_path FROM therapy_sessions "
            "WHERE patient_id = :pid AND audio_gcs_path IS NOT NULL"
        ),
        {"pid": patient_id},
    ).fetchall()
    objects: list[str] = []
    for (raw,) in rows:
        if raw is None:
            continue
        for part in str(raw).split(","):
            stripped = part.strip()
            if stripped:
                objects.append(stripped)
    return objects


def _resolve_audio_storage() -> tuple[Any, str]:
    """Return (storage provider, bucket name) for session audio. Patched in unit tests."""
    from ..services.file_storage import file_storage_from_settings  # noqa: PLC0415
    from ..settings import get_settings  # noqa: PLC0415

    settings = get_settings()
    return file_storage_from_settings(settings), settings.transcription_audio_bucket


def _delete_audio_blobs(objects: list[str]) -> None:
    """Delete storage objects for the given names.

    Provider deletes are idempotent on missing objects; any other storage
    failure raises so the caller's transaction rolls back.
    """
    if not objects:
        return
    storage, bucket = _resolve_audio_storage()
    for object_name in objects:
        storage.delete(bucket=bucket, object_name=object_name)


def _document_objects_for_patient(conn: Any, schema: str, patient_id: str) -> list[str]:
    """Return GCS object names for the patient's uploaded documents.

    Covers every ``patient_documents`` row for the patient regardless of
    ``deleted_at`` or ``category`` — a hard purge removes all of them. The
    DB rows themselves cascade away with the patients delete; this collects
    the blob names so the objects can be deleted too.
    """
    _validate_schema_name(schema)
    # Operator job: schema validated by _validate_schema_name(); not web-reachable.
    # nosemgrep
    conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
    rows = conn.execute(
        text(
            "SELECT gcs_path FROM patient_documents "
            "WHERE patient_id = :pid AND gcs_path IS NOT NULL"
        ),
        {"pid": patient_id},
    ).fetchall()
    objects: list[str] = []
    for (raw,) in rows:
        stripped = str(raw).strip() if raw is not None else ""
        if stripped:
            objects.append(stripped)
    return objects


def _resolve_documents_storage() -> tuple[Any, str] | None:
    """Return (storage provider, bucket name) for patient documents, or None.

    Patched in unit tests. Self-hosted deployments without the documents
    feature leave ``patient_documents_gcs_bucket`` unset.
    """
    from ..services.file_storage import file_storage_from_settings  # noqa: PLC0415
    from ..settings import get_settings  # noqa: PLC0415

    settings = get_settings()
    bucket_name = settings.patient_documents_gcs_bucket
    if not bucket_name:
        return None
    return file_storage_from_settings(settings), bucket_name


def _delete_document_blobs(objects: list[str]) -> None:
    """Delete patient-document storage objects.

    Provider deletes are idempotent on missing objects; any other storage
    failure raises so the caller's transaction rolls back. If documents
    exist but no bucket is configured, logs loudly rather than silently
    leaving PHI blobs behind.
    """
    if not objects:
        return
    resolved = _resolve_documents_storage()
    if resolved is None:
        logger.error(
            "hard_purge_document_blobs_orphaned count=%s "
            "(patient_documents_gcs_bucket not configured)",
            len(objects),
        )
        return
    storage, bucket = resolved
    for object_name in objects:
        storage.delete(bucket=bucket, object_name=object_name)


def run(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = _parse_argv(argv)

    stub_writer = get_compliance_retention_stub_writer()
    if stub_writer is None:
        logger.info("hard_purge_skip_no_retention_stub_writer")
        return 0

    engine = get_engine()
    purge_before = _parse_purge_before(args.purge_before_raw)

    with engine.connect() as conn:
        if not stub_writer.is_supported(conn):
            logger.error("hard_purge_retention_stub_unsupported")
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
                    row = _patient_row_for_stub(conn, schema_name, patient_id, purge_before)
                    if row is None:
                        continue
                    if not stub_writer.stub_row_exists(conn, patient_id):
                        name = (row["first_name"] + " " + row["last_name"]).strip() or "(unknown)"
                        dob_raw = row.get("date_of_birth") or None
                        if dob_raw == "":
                            dob_raw = None
                        stub_writer.insert_stub(
                            conn,
                            ComplianceRetentionStubPayload(
                                patient_id=str(row["id"]),
                                display_name=name,
                                dob=str(dob_raw) if dob_raw else None,
                                practice_id=practice_id,
                                schema_name=schema_name,
                                reason="PATIENT_PURGED",
                            ),
                        )
                    _append_purge_audit(conn, schema_name, patient_id)
                    audio_objects = _audio_objects_for_patient(conn, schema_name, patient_id)
                    _delete_audio_blobs(audio_objects)
                    document_objects = _document_objects_for_patient(conn, schema_name, patient_id)
                    _delete_document_blobs(document_objects)
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
