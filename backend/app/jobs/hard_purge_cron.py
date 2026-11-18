# Copyright (c) 2026 Pablo Health, LLC. All rights reserved under AGPL-3.0.

"""Soft-delete hard purge job (THERAPY-cgy), stage 2 of the retention model.

Uses the writer from ``get_compliance_retention_stub_writer``. With nothing
registered that is the audit-only default, so ``python -m
app.jobs.hard_purge_cron`` purges on a self-hosted install and the purge's
audit entry is the only record left. Hosted images register
:class:`~app.jobs.hard_purge_retention_stub.SqlComplianceRetentionStubWriter`
from a deployment-specific entrypoint.

Before any deletes, the writer's ``is_supported`` probes the DB; if False,
exit **2** (misconfigured deployment or DDL not applied).

The job is not a principal, and the app role does not bypass row-level
security, so every transaction that reads or deletes patient rows arms
``app.allow_patient_purge`` with ``SET LOCAL``. The policies that setting
opens (see ``_patient_purge_policy_statements`` in ``app.db``) reach only
soft-deleted clients and their rows; nothing else in ``app`` sets it.

Retention stub semantics live in ``hard_purge_retention_stub``. Cloud Storage
cleanup runs inside the per-patient transaction (THERAPY-zu4): blobs referenced
by ``therapy_sessions.audio_gcs_path`` **and** ``patient_documents.gcs_path``
are deleted **before** the clinical-row DELETE commits, so a GCS failure rolls
the whole purge back rather than leaving an audit row claiming a deletion that
didn't happen. The BAA §7 Stage 2 promise covers audio of soft-deleted patients
regardless of the per-practice audio retention slider.

The clinical-row delete removes every table that names the client: the ones
in ``_PATIENT_ROW_TABLES`` by ``patient_id`` (one whose cascade would be
blocked, and the legacy five deleted by name as they always were), the
client's tasks, then the patients row, whose delete cascades to the rest.
Every ``patient_id`` column in a practice schema either cascades from
``patients`` or is on a short reviewed list
(``tests/test_patient_id_foreign_keys.py``), so a new table naming the client
is covered by the database rather than by remembering to extend a list here.
A hard purge leaves zero rows and zero blobs for the client across all
surfaces; cascades never touch storage, so the blobs are collected and
deleted first.

Tasks are the one table where the client's rows and the clinician's meet.
A to-do about the client goes, and so do its steps and the files attached
to any of them (``compliance_documents``, which cascade from the task and
whose objects are deleted here first). A compliance task is the clinician's
own licensure or supervision record; one linked to the client is kept, with
its documents and supervision relationship, and the patients delete unlinks
it. See ``_task_document_uris_for_patient``.

Invoked from repo ``backend/``::

    python -m app.jobs.hard_purge_cron --dry-run
    python -m app.jobs.hard_purge_cron --purge-before 2026-01-01T00:00:00Z

Self-hosted installs run it daily from cron (``deploy/vm/crontab``).
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
from ..logging_config import configure_job_logging
from ..models.audit import (
    ACTOR_TYPE_SYSTEM,
    AUDIT_LOG_RETENTION_DAYS,
    AuditAction,
    ResourceType,
)
from .hard_purge_retention_stub import (
    ComplianceRetentionStubPayload,
    get_compliance_retention_stub_writer,
)

logger = logging.getLogger(__name__)

_RETENTION_JOB_USER_ID = "system:retention_job"
_RETENTION_JOB_COMPONENT = "hard_purge"


def _arm_patient_purge(conn: Any) -> None:
    """Open the purge policies for the rest of this transaction only.

    ``SET LOCAL`` outside a transaction is a no-op, so callers hold one
    (``engine.begin()``); the setting goes with the commit or rollback and
    never reaches the pool.
    """
    conn.execute(text("SET LOCAL app.allow_patient_purge = 'on'"))


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
    with engine.begin() as conn:
        # Operator job: schema validated by _validate_schema_name(); not web-reachable.
        # nosemgrep
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _arm_patient_purge(conn)
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
    # The audit row's insert policy wants the writer armed as the row's
    # user_id; the job's own id matches no patient grant, so arming it
    # widens nothing else in the transaction.
    conn.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": _RETENTION_JOB_USER_ID},
    )
    entry_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=AUDIT_LOG_RETENTION_DAYS)
    conn.execute(
        text(
            "INSERT INTO audit_logs ("
            " id, timestamp, expires_at, user_id, actor_type, actor_component, action, "
            " resource_type, resource_id, patient_id, changes"
            ") VALUES ("
            " :id, :ts, :exp, :uid, :actor_type, :component, :action, :rtype, :rid, :pid,"
            " CAST(:changes AS jsonb)"
            ")"
        ),
        {
            "id": entry_id,
            "ts": now,
            "exp": expires_at,
            "uid": _RETENTION_JOB_USER_ID,
            "actor_type": ACTOR_TYPE_SYSTEM,
            "component": _RETENTION_JOB_COMPONENT,
            "action": AuditAction.PATIENT_PURGED.value,
            "rtype": ResourceType.PATIENT.value,
            "rid": patient_id,
            "pid": patient_id,
            "changes": '{"source": "hard_purge_cron"}',
        },
    )


#: Tables holding a client's rows that are deleted by ``patient_id`` before
#: the ``patients`` row. Each is in ``app.db.PATIENT_PURGE_TABLES`` with
#: ``delete``.
#:
#: * Message threads cascade from ``patients``, but a message attachment
#:   holds its document with ``ON DELETE RESTRICT``, and the cascade can
#:   reach the document first and fail the whole purge. Deleting the threads
#:   first takes the messages and attachments with them.
#: * The first five cascade from ``patients`` too; they are deleted here
#:   by name, as they always have been.
#:
#: Everything else naming the client cascades from ``patients``, including
#: intake, charges, the saved payment method, portal sessions and sign-in
#: challenges, and calendar events answered with the client (foreign keys
#: added by revision ``8c5e65a0e56a``). Order matters only for the threads,
#: which must precede the patients row. ``tasks`` is deleted after these, by
#: ``_delete_clinical_rows`` itself: compliance tasks are kept (see
#: ``_task_document_uris_for_patient``).
_PATIENT_ROW_TABLES: tuple[str, ...] = (
    "appointments",
    "notes",
    "therapy_sessions",
    "patient_source_mappings",
    "chat_conversations",
    "patient_message_threads",
)


def _delete_clinical_rows(conn: Any, schema: str, patient_id: str) -> None:
    _validate_schema_name(schema)
    # Operator job: schema validated by _validate_schema_name(); not web-reachable.
    # nosemgrep
    conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
    # chat_messages cascade from their conversation; deleted first anyway so
    # the purge does not lean on that cascade.
    conn.execute(
        text(
            "DELETE FROM chat_messages WHERE conversation_id IN "
            "(SELECT id FROM chat_conversations WHERE patient_id = :pid)"
        ),
        {"pid": patient_id},
    )
    for table in _PATIENT_ROW_TABLES:
        # Table names are this module's constants; the schema was validated above.
        # nosemgrep
        statement = text(f"DELETE FROM {table} WHERE patient_id = :pid")  # noqa: S608
        conn.execute(statement, {"pid": patient_id})
    # ``tasks`` cascades as ``SET NULL``, which would keep a to-do's text,
    # often naming the client, detached from any chart. Its steps and its
    # attached documents cascade from it; run() deleted the documents'
    # objects already. A compliance task is the clinician's record and is
    # kept: the patients delete below unlinks it.
    conn.execute(
        text("DELETE FROM tasks WHERE patient_id = :pid AND category <> 'compliance'"),
        {"pid": patient_id},
    )
    # Everything else that names the client goes with the patients row by
    # foreign-key cascade, which row-level security does not apply to:
    # patient_documents (blobs removed in run() before this commit), the
    # clinical chart tables, claims, intake (answers, signatures and
    # artifacts off their assignment), charges, the saved payment method,
    # portal sessions and challenges, calendar events, and message
    # attachments off their message.
    conn.execute(text("DELETE FROM patients WHERE id = :pid"), {"pid": patient_id})


#: The category of a clinician's own licensure and supervision tasks. Kept by
#: the purge; everything else naming the client is deleted.
_COMPLIANCE_CATEGORY = "compliance"


def _task_document_uris_for_patient(conn: Any, schema: str, patient_id: str) -> list[str]:
    """Return the files attached to the client's tasks that the purge deletes.

    Every task naming the client except a compliance one is deleted by
    ``_delete_clinical_rows``; its steps and their ``compliance_documents``
    rows go with it by cascade. This returns those documents'
    ``storage_uri`` values so run() can delete the objects before the rows:
    a row gone with its object still in storage is a file nothing can find
    or delete again.

    A compliance task linked to the client is the clinician's licensure or
    supervision record, not the client's. It is not deleted, so its
    documents and supervision relationship stay, and the patients delete
    unlinks it (``ON DELETE SET NULL``), as it did before the purge deleted
    any task.

    Tasks and their documents are row-scoped to the clinician who owns them,
    and the purge policies reach only rows that name a soft-deleted client:
    neither a step with no client of its own nor a document. So the job reads
    as each owner in turn, with one fixed query filtered to that owner's
    tasks about this client, and then restores its own id, which matches no
    clinician.
    """
    _validate_schema_name(schema)
    # Operator job: schema validated by _validate_schema_name(); not web-reachable.
    # nosemgrep
    conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
    owners = [
        str(row[0])
        for row in conn.execute(
            text(
                "SELECT DISTINCT user_id FROM tasks "
                "WHERE patient_id = :pid AND category <> :compliance ORDER BY user_id"
            ),
            {"pid": patient_id, "compliance": _COMPLIANCE_CATEGORY},
        ).fetchall()
    ]
    uris: list[str] = []
    try:
        for owner in owners:
            conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": owner}
            )
            rows = conn.execute(
                text(
                    "WITH RECURSIVE doomed AS ("
                    " SELECT id FROM tasks"
                    " WHERE patient_id = :pid AND user_id = CAST(:uid AS uuid)"
                    " AND category <> :compliance"
                    " UNION"
                    " SELECT t.id FROM tasks t JOIN doomed d ON t.parent_task_id = d.id"
                    ") "
                    "SELECT storage_uri FROM compliance_documents "
                    "WHERE compliance_item_id IN (SELECT id FROM doomed)"
                ),
                {"pid": patient_id, "uid": owner, "compliance": _COMPLIANCE_CATEGORY},
            ).fetchall()
            uris.extend(str(raw).strip() for (raw,) in rows if raw and str(raw).strip())
    finally:
        conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": _RETENTION_JOB_USER_ID},
        )
    return uris


def _resolve_compliance_storage() -> Any:
    """Return the compliance-document storage backend. Patched in unit tests.

    Deletes go by each document's own ``storage_uri``, so the backend needs no
    configured root to remove an object written under an earlier one.
    """
    from ..services.compliance_storage import ComplianceStorageBackend  # noqa: PLC0415
    from ..settings import get_settings  # noqa: PLC0415

    settings = get_settings()
    return ComplianceStorageBackend(settings.compliance_documents_storage_root, settings=settings)


def _delete_task_document_blobs(uris: list[str]) -> None:
    """Delete the stored files of documents attached to the client's tasks.

    Deletes are idempotent on missing objects; any other storage failure
    raises so the caller's transaction rolls back and the rows stay.
    """
    if not uris:
        return
    storage = _resolve_compliance_storage()
    for uri in uris:
        storage.delete(uri)


def _audio_objects_for_patient(conn: Any, schema: str, patient_id: str) -> list[str]:
    """Return GCS object names referenced by the patient's sessions.

    ``therapy_sessions.audio_gcs_path`` may hold a single object name or a
    comma-separated pair (stereo: ``"<therapist>,<client>"`` — see
    ``app.routes.sessions``). Empty parts are dropped. What the clinician
    dictated about those sessions afterwards (``session_dictations``) is
    in the same bucket and goes with them.
    """
    _validate_schema_name(schema)
    # Operator job: schema validated by _validate_schema_name(); not web-reachable.
    # nosemgrep
    conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
    rows = conn.execute(
        text(
            "SELECT audio_gcs_path FROM therapy_sessions "
            "WHERE patient_id = :pid AND audio_gcs_path IS NOT NULL "
            "UNION ALL "
            "SELECT audio_path FROM session_dictations WHERE patient_id = :pid"
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


def _delete_speech_sibling_blobs(objects: list[str]) -> None:
    """Delete staged speech-derivative siblings of the given audio objects.

    ``assemblyai_submit`` stages a speech-only copy of each channel next to
    its source object, named ``"<object>.speech.<ext>"``. The transcription
    poller deletes its own copy once the job completes, but this sweeps
    whatever that path missed — a session that never finished polling, for
    instance — by listing each object's prefix rather than relying on a
    name that is never recorded on the row.
    """
    if not objects:
        return
    storage, bucket = _resolve_audio_storage()
    for object_name in objects:
        for sibling in storage.list_names(bucket=bucket, prefix=f"{object_name}.speech."):
            storage.delete(bucket=bucket, object_name=sibling)


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
    configure_job_logging()
    args = _parse_argv(argv)

    stub_writer = get_compliance_retention_stub_writer()
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
                    _arm_patient_purge(conn)
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
                    _delete_speech_sibling_blobs(audio_objects)
                    document_objects = _document_objects_for_patient(conn, schema_name, patient_id)
                    _delete_document_blobs(document_objects)
                    task_documents = _task_document_uris_for_patient(conn, schema_name, patient_id)
                    _delete_task_document_blobs(task_documents)
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
