# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL :class:`PatientPaymentRepository` implementation.

Every query runs on the request's tenant-scoped session, so the practice
boundary is the schema and the per-client boundary is the ``has_patient_access``
row policy on both tables — a clinician with no grant on a client sees no card
and no charges, and cannot write either. There is no additional access
predicate in this file for the same reason there is none in the notes
repository: adding one would put a second, drifting copy of the rule beside the
one the database already enforces.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from ...db.models import PatientChargeRow, PatientPaymentMethodRow
from ...models.payments import CardOnFile, PatientCharge
from ...utcnow import utc_now
from ..patient_payment import PatientPaymentRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _to_card(row: PatientPaymentMethodRow) -> CardOnFile:
    return CardOnFile(
        id=row.id,
        patient_id=row.patient_id,
        stripe_customer_id=row.stripe_customer_id,
        stripe_payment_method_id=row.stripe_payment_method_id,
        card_brand=row.card_brand,
        card_last4=row.card_last4,
        card_exp_month=row.card_exp_month,
        card_exp_year=row.card_exp_year,
    )


def _to_charge(row: PatientChargeRow) -> PatientCharge:
    return PatientCharge(
        id=row.id,
        patient_id=row.patient_id,
        appointment_id=row.appointment_id,
        amount_cents=row.amount_cents,
        currency=row.currency,
        status=row.status,
        status_detail=row.status_detail,
        stripe_payment_intent_id=row.stripe_payment_intent_id,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresPatientPaymentRepository(PatientPaymentRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- card on file ---

    def _card_row(self, patient_id: str) -> PatientPaymentMethodRow | None:
        return (
            self._session.execute(
                select(PatientPaymentMethodRow).where(
                    PatientPaymentMethodRow.patient_id == patient_id
                )
            )
            .scalars()
            .first()
        )

    def get_card_on_file(self, patient_id: str) -> CardOnFile | None:
        row = self._card_row(patient_id)
        return _to_card(row) if row is not None else None

    def start_card_setup(
        self, *, patient_id: str, stripe_customer_id: str, user_id: str
    ) -> CardOnFile:
        row = PatientPaymentMethodRow(
            id=uuid.uuid4().hex,
            patient_id=patient_id,
            stripe_customer_id=stripe_customer_id,
            created_by_user_id=user_id,
            created_at=utc_now(),
        )
        self._session.add(row)
        self._session.commit()
        return _to_card(row)

    def complete_card_setup(  # noqa: PLR0913 — the display triple plus its keys
        self,
        *,
        patient_id: str,
        stripe_payment_method_id: str,
        brand: str | None,
        last4: str | None,
        exp_month: int | None,
        exp_year: int | None,
        user_id: str,
    ) -> CardOnFile | None:
        row = self._card_row(patient_id)
        if row is None:
            return None
        row.stripe_payment_method_id = stripe_payment_method_id
        row.card_brand = brand
        row.card_last4 = last4
        row.card_exp_month = exp_month
        row.card_exp_year = exp_year
        # The clinician who last put a card on file, not the one who first
        # started setup — this row is about the card that is there now.
        row.created_by_user_id = user_id
        row.updated_at = utc_now()
        self._session.commit()
        return _to_card(row)

    # --- charge ledger ---

    def _charge_row(self, charge_id: str) -> PatientChargeRow:
        row = self._session.get(PatientChargeRow, charge_id)
        if row is None:
            # This repository created the row and committed it moments ago, so
            # its absence is a broken invariant, not a caller error.
            msg = f"charge {charge_id!r} vanished between writes"
            raise LookupError(msg)
        return row

    def stage_charge(
        self,
        *,
        patient_id: str,
        appointment_id: str | None,
        amount_cents: int,
        currency: str,
        user_id: str,
    ) -> PatientCharge:
        row = PatientChargeRow(
            id=uuid.uuid4().hex,
            patient_id=patient_id,
            appointment_id=appointment_id,
            amount_cents=amount_cents,
            currency=currency,
            status="pending",
            created_by_user_id=user_id,
            created_at=utc_now(),
        )
        self._session.add(row)
        self._session.flush()
        return _to_charge(row)

    def commit(self) -> None:
        self._session.commit()

    def record_payment_intent(self, charge_id: str, payment_intent_id: str) -> None:
        row = self._charge_row(charge_id)
        row.stripe_payment_intent_id = payment_intent_id
        row.updated_at = utc_now()
        self._session.commit()

    def close_charge(
        self, charge_id: str, *, status: str, status_detail: str | None
    ) -> PatientCharge:
        row = self._charge_row(charge_id)
        row.status = status
        row.status_detail = status_detail
        row.updated_at = utc_now()
        self._session.commit()
        return _to_charge(row)

    def list_charges(self, patient_id: str) -> list[PatientCharge]:
        rows = (
            self._session.execute(
                select(PatientChargeRow)
                .where(PatientChargeRow.patient_id == patient_id)
                .order_by(PatientChargeRow.created_at.desc())
            )
            .scalars()
            .all()
        )
        return [_to_charge(row) for row in rows]
