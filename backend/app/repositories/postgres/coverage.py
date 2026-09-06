# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL implementations of the payer and coverage repositories.

Every query runs on the request's tenant-scoped session. The practice
boundary is the schema for both tables; the per-client boundary on
``patient_coverage`` is the ``has_patient_access`` row policy, which is why
there is no access predicate written here — the database already enforces it,
and a second copy beside it would only drift.

``payers`` has no row policy on purpose (it is practice-level, see
``_CORE_NOT_ROW_SCOPED``), so its reads are the whole list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ...db.models import PatientCoverageRow, PayerRow
from ...models.coverage import PatientCoverage, Payer
from ...utcnow import utc_now
from ..coverage import ActiveCoverageExistsError, PatientCoverageRepository, PayerRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_PAYER_FIELDS = (
    "name",
    "payer_id",
    "clearinghouse_payer_id",
    "is_carveout",
    "carveout_of",
    "enrollment_status",
    "timely_filing_days",
    "corrected_claim_days",
    "appeal_days",
)

_COVERAGE_FIELDS = (
    "patient_id",
    "payer_id",
    "member_id",
    "group_number",
    "subscriber_relationship",
    "subscriber_first_name",
    "subscriber_last_name",
    "subscriber_date_of_birth",
    "subscriber_sex",
    "subscriber_address_line1",
    "subscriber_address_line2",
    "subscriber_city",
    "subscriber_state",
    "subscriber_postal_code",
    "plan_name",
    "active",
    "last_271",
    "verified_at",
)

_ACTIVE_COVERAGE_INDEX = "ux_patient_coverage_active_primary"


def _to_payer(row: PayerRow) -> Payer:
    return Payer(
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        **{name: getattr(row, name) for name in _PAYER_FIELDS},
    )


def _to_coverage(row: PatientCoverageRow) -> PatientCoverage:
    return PatientCoverage(
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        **{name: getattr(row, name) for name in _COVERAGE_FIELDS},
    )


class PostgresPayerRepository(PayerRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self) -> list[Payer]:
        rows = self._session.execute(select(PayerRow).order_by(func.lower(PayerRow.name))).scalars()
        return [_to_payer(row) for row in rows]

    def get(self, payer_row_id: str) -> Payer | None:
        row = self._session.get(PayerRow, payer_row_id)
        return _to_payer(row) if row is not None else None

    def find_typed(self, name: str, payer_id: str | None) -> Payer | None:
        stmt = select(PayerRow).where(func.lower(PayerRow.name) == name.strip().lower())
        if payer_id:
            stmt = stmt.where(func.upper(PayerRow.payer_id) == payer_id.strip().upper())
        row = self._session.execute(stmt.order_by(PayerRow.created_at)).scalars().first()
        return _to_payer(row) if row is not None else None

    def create(self, payer: Payer) -> Payer:
        row = PayerRow(
            id=payer.id,
            created_at=payer.created_at,
            updated_at=payer.updated_at,
            **{name: getattr(payer, name) for name in _PAYER_FIELDS},
        )
        self._session.add(row)
        self._session.flush()
        return _to_payer(row)

    def update(self, payer: Payer) -> Payer:
        row = self._session.get(PayerRow, payer.id)
        if row is None:
            msg = f"payer {payer.id!r} not found for update"
            raise LookupError(msg)
        for name in _PAYER_FIELDS:
            setattr(row, name, getattr(payer, name))
        row.updated_at = utc_now()
        self._session.flush()
        return _to_payer(row)


class PostgresPatientCoverageRepository(PatientCoverageRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, coverage_id: str) -> PatientCoverage | None:
        row = self._session.get(PatientCoverageRow, coverage_id)
        return _to_coverage(row) if row is not None else None

    def get_active(self, patient_id: str) -> PatientCoverage | None:
        row = (
            self._session.execute(
                select(PatientCoverageRow).where(
                    PatientCoverageRow.patient_id == patient_id,
                    PatientCoverageRow.active.is_(True),
                )
            )
            .scalars()
            .first()
        )
        return _to_coverage(row) if row is not None else None

    def create(self, coverage: PatientCoverage) -> PatientCoverage:
        row = PatientCoverageRow(
            id=coverage.id,
            created_at=coverage.created_at,
            updated_at=coverage.updated_at,
            **{name: getattr(coverage, name) for name in _COVERAGE_FIELDS},
        )
        self._session.add(row)
        self._flush_active_rule(coverage.patient_id)
        return _to_coverage(row)

    def update(self, coverage: PatientCoverage) -> PatientCoverage:
        row = self._session.get(PatientCoverageRow, coverage.id)
        if row is None:
            msg = f"coverage {coverage.id!r} not found for update"
            raise LookupError(msg)
        for name in _COVERAGE_FIELDS:
            setattr(row, name, getattr(coverage, name))
        row.updated_at = utc_now()
        self._flush_active_rule(coverage.patient_id)
        return _to_coverage(row)

    def _flush_active_rule(self, patient_id: str) -> None:
        """Flush, turning the partial unique index's refusal into a domain error.

        The transaction is left as SQLAlchemy leaves it after a failed flush;
        the caller's request rolls it back.
        """
        try:
            self._session.flush()
        except IntegrityError as exc:
            if _ACTIVE_COVERAGE_INDEX in str(exc.orig):
                raise ActiveCoverageExistsError(patient_id) from exc
            raise
