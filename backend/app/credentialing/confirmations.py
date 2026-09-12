# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Recording that a clinician looked at a pre-filled value and said yes or no.

The intake's first tier asks nothing. It fills fields from NPPES, the public
PECOS file, the exclusion lists and what the practice already stores, and asks
only whether each is right. This is where that answer goes.

Two reasons it is a record and not a UI state. A payer application distinguishes
self-reported data from verified data, and "she confirmed this on this date,
against this source" is what moves a value onto the second side of that line.
And a value she corrected is a value the source has wrong — worth keeping
whether or not anything acts on it yet.

The shape is the mirror image of :mod:`app.credentialing.disclosures`. There,
``true`` is the answer that needs explaining. Here ``true`` means "correct" and
needs nothing, while ``false`` is the one that must carry a correction. One row
per field, updated in place: the question is always "is this right now", never
a history of what she was shown.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, select

from ..db.models import CREDENTIAL_CONFIRMATION_SOURCES, CredentialConfirmationRow

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session


class CorrectionRequiredError(ValueError):
    """A field was marked wrong with nothing said about what it should be.

    The schema refuses it too. Raised here first so the caller gets the field
    key back rather than an IntegrityError naming a check constraint.
    """

    def __init__(self, field_key: str) -> None:
        super().__init__(
            f"Confirmation {field_key!r} was rejected with no correction; "
            "a value marked wrong is only useful with the right one beside it."
        )


class UnknownSourceError(ValueError):
    """A provenance the schema does not recognise.

    Caught here rather than at the check constraint because the surface shows
    the source to the clinician — an unrecognised one means she is being told
    where a value came from in words nothing else in the system uses.
    """

    def __init__(self, source: str) -> None:
        super().__init__(
            f"Unknown confirmation source {source!r}; "
            f"expected one of {', '.join(CREDENTIAL_CONFIRMATION_SOURCES)}."
        )


def record(  # noqa: PLR0913 — session + the five things a confirmation is
    session: Session,
    user_id: str,
    *,
    field_key: str,
    source: str,
    confirmed: bool,
    presented_value: str | None = None,
    correction: str | None = None,
    confirmed_at: datetime | None = None,
) -> CredentialConfirmationRow:
    """Store a confirmation, replacing any earlier one for the same field.

    ``presented_value`` is what she was shown. For a field with a home column
    that column stays the record and this is a snapshot, so a later divergence
    is visible; for the few Tier-0 fields with no home column it IS the record.
    Does not commit — the caller owns the transaction.
    """
    if source not in CREDENTIAL_CONFIRMATION_SOURCES:
        raise UnknownSourceError(source)
    if not confirmed and not (correction and correction.strip()):
        raise CorrectionRequiredError(field_key)

    now = datetime.now(UTC)
    row = session.scalars(
        select(CredentialConfirmationRow).where(
            and_(
                CredentialConfirmationRow.user_id == user_id,
                CredentialConfirmationRow.field_key == field_key,
            )
        )
    ).one_or_none()

    if row is None:
        row = CredentialConfirmationRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            field_key=field_key,
            created_at=now,
            updated_at=now,
        )
        session.add(row)

    row.source = source
    row.presented_value = presented_value
    row.confirmed = confirmed
    row.correction = None if confirmed else correction
    row.confirmed_at = confirmed_at or now
    row.updated_at = now
    session.flush()
    return row


def list_for(session: Session, user_id: str) -> Sequence[CredentialConfirmationRow]:
    """Every confirmation this clinician has given, oldest first."""
    return session.scalars(
        select(CredentialConfirmationRow)
        .where(CredentialConfirmationRow.user_id == user_id)
        .order_by(CredentialConfirmationRow.created_at)
    ).all()


def corrections(session: Session, user_id: str) -> Sequence[CredentialConfirmationRow]:
    """The ones she said were wrong — the queue a lookup owner has to work.

    A correction means a public source disagrees with her, which is a fact
    about the source as much as about the record.
    """
    return session.scalars(
        select(CredentialConfirmationRow)
        .where(
            and_(
                CredentialConfirmationRow.user_id == user_id,
                CredentialConfirmationRow.confirmed.is_(False),
            )
        )
        .order_by(CredentialConfirmationRow.confirmed_at)
    ).all()
