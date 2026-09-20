# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL InstrumentLicenseRepository implementation.

Tenant scope is the session's ``search_path``, set before the request
reaches here, so none of these queries carries a practice predicate — the
same contract as every other repository in this package. There is no
``has_patient_access`` call because no row here belongs to a patient.

``revoked_at IS NULL`` appears in both reads rather than being folded in
Python: it is the same predicate the partial unique index is built on, so
the question the code asks and the question the database enforces are
written the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from ...db.models import InstrumentLicenseAttestationRow
from ..instrument_license import InstrumentLicenseRepository

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session


def _to_dict(row: InstrumentLicenseAttestationRow) -> dict[str, object]:
    return {
        "id": row.id,
        "instrument_code": row.instrument_code,
        "attested_by": row.attested_by,
        "attested_at": row.attested_at,
        "license_reference": row.license_reference,
        "notes": row.notes,
        "revoked_at": row.revoked_at,
    }


class PostgresInstrumentLicenseRepository(InstrumentLicenseRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, row: dict[str, object]) -> dict[str, object]:
        orm_row = InstrumentLicenseAttestationRow(
            id=str(row["id"]),
            instrument_code=str(row["instrument_code"]),
            attested_by=str(row["attested_by"]),
            attested_at=row["attested_at"],  # type: ignore[arg-type]
            license_reference=row.get("license_reference"),  # type: ignore[arg-type]
            notes=row.get("notes"),  # type: ignore[arg-type]
            revoked_at=None,
        )
        self._session.add(orm_row)
        self._session.flush()
        return _to_dict(orm_row)

    def list_active(self) -> list[dict[str, object]]:
        rows = (
            self._session.execute(
                select(InstrumentLicenseAttestationRow)
                .where(InstrumentLicenseAttestationRow.revoked_at.is_(None))
                .order_by(
                    InstrumentLicenseAttestationRow.attested_at,
                    InstrumentLicenseAttestationRow.id,
                )
            )
            .scalars()
            .all()
        )
        return [_to_dict(row) for row in rows]

    def active_for_code(self, instrument_code: str) -> dict[str, object] | None:
        row = (
            self._session.execute(
                select(InstrumentLicenseAttestationRow).where(
                    InstrumentLicenseAttestationRow.instrument_code == instrument_code,
                    InstrumentLicenseAttestationRow.revoked_at.is_(None),
                )
            )
            .scalars()
            .first()
        )
        return _to_dict(row) if row else None

    def revoke(self, attestation_id: str, revoked_at: datetime) -> dict[str, object] | None:
        row = self._session.get(InstrumentLicenseAttestationRow, attestation_id)
        if row is None:
            return None
        row.revoked_at = revoked_at
        self._session.flush()
        return _to_dict(row)
