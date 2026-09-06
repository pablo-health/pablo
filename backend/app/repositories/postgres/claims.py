# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL implementation of the claim repository.

Every query runs on the request's tenant-scoped session. The per-client
boundary on both ``claims`` and ``claim_lines`` is the ``has_patient_access``
row policy, which is why no access predicate is written here — the
database enforces it, and a second copy beside it would only drift.

The two snapshot columns are JSON; the typed snapshot models serialise to
and from them at this boundary and nowhere else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from ...db.models import ClaimLineRow, ClaimRow
from ...models.claims import BillingSnapshot, Claim, ClaimLine, SubscriberSnapshot
from ...utcnow import utc_now
from ..claims import ClaimRepository

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.orm import Session

_HEADER_FIELDS = (
    "control_number",
    "patient_id",
    "coverage_id",
    "payer_id",
    "state",
    "frequency_code",
    "parent_claim_id",
    "total_charge_cents",
    "total_paid_cents",
    "diagnosis_codes",
    "place_of_service",
    "submitted_at",
    "payer_accepted_at",
    "adjudicated_at",
)

#: Header fields that may change after the claim is built.
_MUTABLE_HEADER_FIELDS = (
    "state",
    "total_paid_cents",
    "submitted_at",
    "payer_accepted_at",
    "adjudicated_at",
)

_LINE_FIELDS = (
    "claim_id",
    "patient_id",
    "appointment_id",
    "line_number",
    "line_control_number",
    "service_date",
    "cpt",
    "modifiers",
    "units",
    "charge_cents",
    "dx_pointers",
    "telehealth",
    "allowed_cents",
    "paid_cents",
    "patient_resp_cents",
    "adjustments",
)

#: Line fields remittance posting writes back.
_MUTABLE_LINE_FIELDS = ("allowed_cents", "paid_cents", "patient_resp_cents", "adjustments")


def _to_line(row: ClaimLineRow) -> ClaimLine:
    return ClaimLine(
        id=row.id,
        created_at=row.created_at,
        **{name: getattr(row, name) for name in _LINE_FIELDS},
    )


def _to_claim(row: ClaimRow, lines: list[ClaimLineRow]) -> Claim:
    return Claim(
        id=row.id,
        billing_snapshot=BillingSnapshot.model_validate(row.billing_snapshot),
        subscriber_snapshot=SubscriberSnapshot.model_validate(row.subscriber_snapshot),
        created_at=row.created_at,
        updated_at=row.updated_at,
        lines=[_to_line(line) for line in sorted(lines, key=lambda line: line.line_number)],
        **{name: getattr(row, name) for name in _HEADER_FIELDS},
    )


class PostgresClaimRepository(ClaimRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, claim_id: str) -> Claim | None:
        row = self._session.get(ClaimRow, claim_id)
        if row is None:
            return None
        return _to_claim(row, self._lines_for([row.id]).get(row.id, []))

    def list_by_patient(self, patient_id: str) -> list[Claim]:
        rows = (
            self._session.execute(
                select(ClaimRow)
                .where(ClaimRow.patient_id == patient_id)
                .order_by(ClaimRow.created_at.desc(), ClaimRow.id)
            )
            .scalars()
            .all()
        )
        lines = self._lines_for([row.id for row in rows])
        return [_to_claim(row, lines.get(row.id, [])) for row in rows]

    def list_for_export(self, from_date: date, to_date: date) -> list[Claim]:
        dated_in_range = select(ClaimLineRow.claim_id).where(
            ClaimLineRow.service_date >= from_date, ClaimLineRow.service_date <= to_date
        )
        rows = (
            self._session.execute(
                select(ClaimRow)
                .where(ClaimRow.state != "draft", ClaimRow.id.in_(dated_in_range))
                .order_by(ClaimRow.created_at, ClaimRow.id)
            )
            .scalars()
            .all()
        )
        lines = self._lines_for([row.id for row in rows])
        return [_to_claim(row, lines.get(row.id, [])) for row in rows]

    def list_all(
        self,
        *,
        state: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[Claim]:
        query = select(ClaimRow)
        if state is not None:
            query = query.where(ClaimRow.state == state)
        if from_date is not None or to_date is not None:
            dated_in_range = select(ClaimLineRow.claim_id)
            if from_date is not None:
                dated_in_range = dated_in_range.where(ClaimLineRow.service_date >= from_date)
            if to_date is not None:
                dated_in_range = dated_in_range.where(ClaimLineRow.service_date <= to_date)
            query = query.where(ClaimRow.id.in_(dated_in_range))
        rows = (
            self._session.execute(query.order_by(ClaimRow.created_at.desc(), ClaimRow.id))
            .scalars()
            .all()
        )
        lines = self._lines_for([row.id for row in rows])
        return [_to_claim(row, lines.get(row.id, [])) for row in rows]

    def latest_by_appointment(self, appointment_ids: list[str]) -> dict[str, Claim]:
        if not appointment_ids:
            return {}
        pairs = self._session.execute(
            select(ClaimRow, ClaimLineRow.appointment_id)
            .join(ClaimLineRow, ClaimLineRow.claim_id == ClaimRow.id)
            .where(ClaimLineRow.appointment_id.in_(appointment_ids))
            .order_by(ClaimRow.created_at.desc(), ClaimRow.id)
        ).all()
        newest_rows: dict[str, ClaimRow] = {}
        for row, appointment_id in pairs:
            if appointment_id is not None and appointment_id not in newest_rows:
                newest_rows[appointment_id] = row
        lines = self._lines_for(list({row.id for row in newest_rows.values()}))
        return {
            appointment_id: _to_claim(row, lines.get(row.id, []))
            for appointment_id, row in newest_rows.items()
        }

    def create(self, claim: Claim) -> Claim:
        row = ClaimRow(
            id=claim.id,
            billing_snapshot=claim.billing_snapshot.model_dump(mode="json"),
            subscriber_snapshot=claim.subscriber_snapshot.model_dump(mode="json"),
            created_at=claim.created_at,
            updated_at=claim.updated_at,
            **{name: getattr(claim, name) for name in _HEADER_FIELDS},
        )
        self._session.add(row)
        # No ORM relationship ties the two tables, so nothing tells the unit
        # of work the header must land first; flush it before the lines.
        self._session.flush()
        line_rows = [
            ClaimLineRow(
                id=line.id,
                created_at=line.created_at,
                **{name: getattr(line, name) for name in _LINE_FIELDS},
            )
            for line in claim.lines
        ]
        self._session.add_all(line_rows)
        self._session.flush()
        return _to_claim(row, line_rows)

    def update(self, claim: Claim) -> Claim:
        row = self._session.get(ClaimRow, claim.id)
        if row is None:
            msg = f"claim {claim.id!r} not found for update"
            raise LookupError(msg)
        for name in _MUTABLE_HEADER_FIELDS:
            setattr(row, name, getattr(claim, name))
        row.updated_at = utc_now()

        line_rows = self._lines_for([claim.id]).get(claim.id, [])
        by_id = {line.id: line for line in claim.lines}
        for line_row in line_rows:
            line = by_id.get(line_row.id)
            if line is None:
                continue
            for name in _MUTABLE_LINE_FIELDS:
                setattr(line_row, name, getattr(line, name))
        self._session.flush()
        return _to_claim(row, line_rows)

    def _lines_for(self, claim_ids: list[str]) -> dict[str, list[ClaimLineRow]]:
        if not claim_ids:
            return {}
        rows = (
            self._session.execute(
                select(ClaimLineRow)
                .where(ClaimLineRow.claim_id.in_(claim_ids))
                .order_by(ClaimLineRow.claim_id, ClaimLineRow.line_number)
            )
            .scalars()
            .all()
        )
        grouped: dict[str, list[ClaimLineRow]] = {}
        for row in rows:
            grouped.setdefault(row.claim_id, []).append(row)
        return grouped
