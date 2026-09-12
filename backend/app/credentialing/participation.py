# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Moving a panel's status, and recording that it moved.

The one writer of ``payer_participations.status``. Every transition goes through
:func:`transition`, which writes the event row in the same flush as the status
change — so the history can never disagree with the current value, and a panel
that went quiet three months ago can be found.

What this module is NOT about: ``payers.enrollment_status`` and
``payer_enrollments`` are the practice's electronic connection to a payer
(837/835/270). Nothing here reads or writes either.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, select

from ..db.models import (
    PARTICIPATION_STATUSES,
    PayerParticipationEventRow,
    PayerParticipationRow,
)
from ..models.audit import AuditAction, ResourceType

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.orm import Session

    from ..models.user import User
    from ..services.audit_service import AuditService


#: The status a payer starts at for every clinician, whether or not a row
#: exists. Absence of a row and ``out_of_network`` mean the same thing, which
#: is why nothing provisions rows for payers nobody has applied to.
DEFAULT_STATUS = "out_of_network"

#: Statuses that mean the payer has finished verifying the clinician. Reached
#: ``credentialed`` or beyond, in other words — and ``single_case_agreement``
#: is NOT among them, because an SCA is agreed without any verification at all.
_CREDENTIALED_STATUSES: frozenset[str] = frozenset({"credentialed", "contracted", "in_network"})

#: Statuses that mean a participation agreement exists.
_CONTRACTED_STATUSES: frozenset[str] = frozenset({"contracted", "in_network"})


class UnknownStatusError(ValueError):
    """A status outside :data:`PARTICIPATION_STATUSES` was asked for.

    Raised rather than letting the database's CHECK constraint refuse it, so the
    caller gets the valid set back instead of an IntegrityError naming a
    constraint.
    """

    def __init__(self, status: str) -> None:
        super().__init__(
            f"Unknown participation status {status!r}; expected one of "
            f"{sorted(PARTICIPATION_STATUSES)}"
        )


@dataclass(frozen=True)
class ParticipationSnapshot:
    """Where one panel stands, for a caller that does not want the ORM row."""

    id: str
    user_id: str
    payer_id: str
    status: str
    credentialed_at: date | None
    contracted_at: date | None
    effective_date: date | None
    termination_date: date | None
    recredentialing_due_at: date | None
    provider_id_with_payer: str | None

    @property
    def credentialed_not_contracted(self) -> bool:
        """The trap state: verified by the payer, with no agreement to bill under.

        Common, silent, and worth years of lost revenue — a clinician believes
        she is paneled because somebody told her she was approved, and the
        contract never arrived.
        """
        return self.status in _CREDENTIALED_STATUSES and self.status not in _CONTRACTED_STATUSES


def get(session: Session, user_id: str, payer_id: str) -> PayerParticipationRow | None:
    """This clinician's row for this payer, or ``None`` when she has never applied."""
    return session.execute(
        select(PayerParticipationRow).where(
            and_(
                PayerParticipationRow.user_id == user_id,
                PayerParticipationRow.payer_id == payer_id,
            )
        )
    ).scalar_one_or_none()


def status_for(session: Session, user_id: str, payer_id: str) -> str:
    """Where she stands with this payer, defaulting to out-of-network.

    What decides whether a session produces a claim or a superbill, so it
    answers for a payer with no row rather than making every caller handle
    ``None``.
    """
    row = get(session, user_id, payer_id)
    return row.status if row is not None else DEFAULT_STATUS


def transition(  # noqa: PLR0913 — service deps + keyword-only audit fields
    session: Session,
    user: User,
    audit: AuditService,
    *,
    payer_id: str,
    to_status: str,
    occurred_at: datetime | None = None,
    note: str | None = None,
    detail: dict[str, object] | None = None,
    request: Request | None = None,
    **dates: date | None,
) -> PayerParticipationRow:
    """Move a panel to ``to_status``, writing its event row in the same flush.

    Creates the participation if this is the first thing that ever happened
    with this payer; the creating event carries ``from_status`` NULL, which is
    how "she applied" is told apart from "she was moved off out_of_network".

    ``dates`` accepts any of ``credentialed_at``, ``contracted_at``,
    ``effective_date``, ``termination_date``, ``recredentialing_due_at`` and
    ``provider_id_with_payer`` — the facts a transition usually arrives with.
    They are applied whether or not the status changed, because a payer
    supplying an effective date for a panel already in ``in_network`` is a real
    and ordinary event.

    A no-op transition (same status, no dates) still writes an event row. That
    is deliberate: a status check that came back "still pending" IS the
    information — it says somebody looked on that date and the payer had not
    moved, which is exactly what a stall report needs.

    Does not commit; the caller owns the transaction.
    """
    if to_status not in PARTICIPATION_STATUSES:
        raise UnknownStatusError(to_status)

    allowed = {
        "credentialed_at",
        "contracted_at",
        "effective_date",
        "termination_date",
        "recredentialing_due_at",
        "provider_id_with_payer",
    }
    unknown = sorted(set(dates) - allowed)
    if unknown:
        msg = f"Unknown participation field(s): {unknown}"
        raise ValueError(msg)

    now = datetime.now(UTC)
    moment = occurred_at or now

    row = get(session, user.id, payer_id)
    from_status: str | None
    if row is None:
        from_status = None
        row = PayerParticipationRow(
            id=str(uuid.uuid4()),
            user_id=user.id,
            payer_id=payer_id,
            status=to_status,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        from_status = row.status
        row.status = to_status
        row.updated_at = now

    for field, value in dates.items():
        setattr(row, field, value)

    # Flush the participation before the event that references it. The two
    # tables have no ORM ``relationship()``, so a single flush does not know one
    # depends on the other and will happily insert the event first — which the
    # foreign key refuses. Same transaction either way, so the pair is still
    # atomic: a failure past this point rolls both back.
    session.flush()

    session.add(
        PayerParticipationEventRow(
            id=str(uuid.uuid4()),
            participation_id=row.id,
            user_id=user.id,
            from_status=from_status,
            to_status=to_status,
            occurred_at=moment,
            note=note,
            detail=detail or {},
            created_at=now,
        )
    )
    session.flush()

    audit.log(
        AuditAction.PAYER_PARTICIPATION_TRANSITIONED,
        user=user,
        request=request,
        resource_type=ResourceType.PAYER_PARTICIPATION,
        resource_id=row.id,
        changes={
            "payer_id": payer_id,
            "from_status": from_status,
            "to_status": to_status,
        },
    )
    return row


def history(session: Session, participation_id: str) -> list[PayerParticipationEventRow]:
    """Every transition of one panel, oldest first.

    Ordered by ``occurred_at`` then ``created_at``: two events can share a
    moment when a backfill records a payer's letter dated the same day as the
    call that chased it, and insertion order is the tiebreak that keeps the
    reconstruction deterministic.
    """
    return list(
        session.execute(
            select(PayerParticipationEventRow)
            .where(PayerParticipationEventRow.participation_id == participation_id)
            .order_by(
                PayerParticipationEventRow.occurred_at,
                PayerParticipationEventRow.created_at,
                PayerParticipationEventRow.id,
            )
        )
        .scalars()
        .all()
    )


def credentialed_not_contracted(session: Session, user_id: str) -> list[PayerParticipationRow]:
    """Panels this clinician is verified for and cannot bill under.

    The query the tracker exists for. Kept here rather than in a route so the
    definition of the trap state lives beside the state machine that produces
    it, and so a reminder job and a dashboard cannot drift on what it means.
    """
    return list(
        session.execute(
            select(PayerParticipationRow)
            .where(
                and_(
                    PayerParticipationRow.user_id == user_id,
                    PayerParticipationRow.status.in_(sorted(_CREDENTIALED_STATUSES)),
                    PayerParticipationRow.status.not_in(sorted(_CONTRACTED_STATUSES)),
                )
            )
            .order_by(PayerParticipationRow.credentialed_at, PayerParticipationRow.id)
        )
        .scalars()
        .all()
    )


def snapshot(row: PayerParticipationRow) -> ParticipationSnapshot:
    """Detach a row into a plain value a caller can hold past the session."""
    return ParticipationSnapshot(
        id=row.id,
        user_id=row.user_id,
        payer_id=row.payer_id,
        status=row.status,
        credentialed_at=row.credentialed_at,
        contracted_at=row.contracted_at,
        effective_date=row.effective_date,
        termination_date=row.termination_date,
        recredentialing_due_at=row.recredentialing_due_at,
        provider_id_with_payer=row.provider_id_with_payer,
    )
