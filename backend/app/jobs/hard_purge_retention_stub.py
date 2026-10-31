# Copyright (c) 2026 Pablo Health, LLC. All rights reserved under AGPL-3.0.

"""Pluggable compliance **minimal retention stub** writer for hard-purge (THERAPY-cgy).

With nothing registered the purge uses :class:`AuditOnlyRetentionStubWriter`:
the record that a client was purged is the job's own audit entry (internal
patient ID and purge time), and no name or date of birth survives. That is
the open-source build's behaviour, and it needs no table beyond
``audit_logs``.

Hosted images register :class:`SqlComplianceRetentionStubWriter` through
``register_compliance_retention_stub_writer`` from a deployment-specific
entrypoint before running the job; an explicit registration always wins.
It writes into ``compliance.patient_identity_tombstone``, which only the
hosted schema has, so it is never the default.

SQL identifiers retain their legacy DDL names
(``patient_identity_tombstone``, ``tombstoned_*``); product language
remains "minimal retention stub."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import text

from ..models.audit import AUDIT_LOG_RETENTION_DAYS


@dataclass(frozen=True, slots=True)
class ComplianceRetentionStubPayload:
    patient_id: str
    display_name: str
    dob: str | None
    practice_id: str
    schema_name: str
    reason: str


class ComplianceRetentionStubWriter(Protocol):
    """Hosted extension point — implement and register before running hard-purge."""

    def is_supported(self, conn: Any) -> bool:
        """Return True when stub DML may run against this database (DDL present)."""

    def stub_row_exists(self, conn: Any, patient_id: str) -> bool:
        pass

    def insert_stub(self, conn: Any, stub: ComplianceRetentionStubPayload) -> None:
        pass


@dataclass
class _WriterRegistry:
    writer: ComplianceRetentionStubWriter | None = None


_registry = _WriterRegistry()


def register_compliance_retention_stub_writer(writer: ComplianceRetentionStubWriter | None) -> None:
    """Set or clear the process-global writer (hosted entrypoint or tests)."""

    _registry.writer = writer


def get_compliance_retention_stub_writer() -> ComplianceRetentionStubWriter:
    return _registry.writer or AuditOnlyRetentionStubWriter()


class AuditOnlyRetentionStubWriter:
    """The default: keep nothing beyond the purge's own audit entry."""

    def is_supported(self, conn: Any) -> bool:  # noqa: ARG002 — the protocol's signature
        return True

    def stub_row_exists(
        self,
        conn: Any,  # noqa: ARG002 — the protocol's signature, unused here
        patient_id: str,  # noqa: ARG002
    ) -> bool:
        return False

    def insert_stub(
        self,
        conn: Any,  # noqa: ARG002 — the protocol's signature, unused here
        stub: ComplianceRetentionStubPayload,  # noqa: ARG002
    ) -> None:
        return None


def _compliance_schema_exists(conn: Any) -> bool:
    row = conn.execute(
        text("SELECT 1 FROM information_schema.schemata WHERE schema_name = 'compliance'")
    ).fetchone()
    return row is not None


class SqlComplianceRetentionStubWriter:
    """Hosted implementation against the legacy compliance DDL."""

    def is_supported(self, conn: Any) -> bool:
        if not _compliance_schema_exists(conn):
            return False
        row = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'compliance' "
                "AND table_name = 'patient_identity_tombstone'"
            ),
        ).fetchone()
        return row is not None

    def stub_row_exists(self, conn: Any, patient_id: str) -> bool:
        row = conn.execute(
            text("SELECT 1 FROM compliance.patient_identity_tombstone WHERE patient_id = :pid"),
            {"pid": patient_id},
        ).fetchone()
        return row is not None

    def insert_stub(self, conn: Any, stub: ComplianceRetentionStubPayload) -> None:
        expires = datetime.now(UTC) + timedelta(days=AUDIT_LOG_RETENTION_DAYS)
        base_cols = (
            "INSERT INTO compliance.patient_identity_tombstone ("
            " patient_id, name, dob, mrn, tenant_id_at_offboard, "
            " original_practice_schema, tombstoned_at, tombstoned_reason, expires_at"
            ") VALUES ("
        )
        tail = " CAST(NULL AS text), :tenant, :schema,  NOW(), :reason, :exp)"
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
            # Operator job: SQL from fixed fragments, all values bound params.
            # nosemgrep
            conn.execute(text(sql), {**params_base, "dob": stub.dob})
        else:
            sql = base_cols + " :pid, :name, NULL::date," + tail
            # Operator job: SQL from fixed fragments, all values bound params.
            # nosemgrep
            conn.execute(text(sql), params_base)
