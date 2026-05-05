# Copyright (c) 2026 Pablo Health, LLC. All rights reserved under AGPL-3.0.

"""Pluggable compliance **minimal retention stub** writer for hard-purge (THERAPY-cgy).

Pablo Core ships the SQLAlchemy implementation (:class:`SqlComplianceRetentionStubWriter`)
but does **not** register it at import time. Hosted images call
``register_compliance_retention_stub_writer`` from ``saas.bin.hard_purge`` before
running the purge job so Core self-hosters never accidentally execute compliance
DML with no DDL present.

SQL identifiers still use legacy SaaS DDL names (``patient_identity_tombstone``,
``tombstoned_*``); product language remains "minimal retention stub."
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
        ...


@dataclass
class _WriterRegistry:
    writer: ComplianceRetentionStubWriter | None = None


_registry = _WriterRegistry()


def register_compliance_retention_stub_writer(writer: ComplianceRetentionStubWriter | None) -> None:
    """Set or clear the process-global writer (hosted entrypoint or tests)."""

    _registry.writer = writer


def get_compliance_retention_stub_writer() -> ComplianceRetentionStubWriter | None:
    return _registry.writer


def _compliance_schema_exists(conn: Any) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'compliance'"
        )
    ).fetchone()
    return row is not None


class SqlComplianceRetentionStubWriter:
    """Default implementation for SaaS compliance DDL (legacy table name)."""

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
            text(
                "SELECT 1 FROM compliance.patient_identity_tombstone "
                "WHERE patient_id = :pid"
            ),
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
