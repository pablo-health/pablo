# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Answering the attestation questions, and keeping the wording they were asked in.

Every disclosure answer is stored against a ``(question_key, question_version)``
pair. Recording an answer to a new version ADDS a row rather than replacing the
old one, so what she affirmed in 2026 stays readable after the question is
reworded in 2027.

That is why :func:`record` never updates across versions. An answer is an
attestation about a specific sentence; re-pointing it at a different sentence
would make the record say something she never said.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, select

from ..db.models import CredentialDisclosureRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class ExplanationRequiredError(ValueError):
    """A ``True`` answer arrived with no explanation.

    The schema refuses it too. Raised here first so the caller gets the question
    key back rather than an IntegrityError naming a check constraint.
    """

    def __init__(self, question_key: str) -> None:
        super().__init__(
            f"Disclosure {question_key!r} answered yes with no explanation; "
            "a payer rejects an unexplained affirmative."
        )


def record(  # noqa: PLR0913 — service deps + keyword-only answer fields
    session: Session,
    user_id: str,
    *,
    question_key: str,
    question_version: int,
    answer: bool,
    explanation: str | None = None,
    answered_at: datetime | None = None,
) -> CredentialDisclosureRow:
    """Store an answer, or correct one already given for this exact version.

    Correcting within a version updates in place: she is amending an answer to a
    sentence she has already read. Answering a NEW version inserts, because that
    is a different sentence. Does not commit.
    """
    if answer and not (explanation and explanation.strip()):
        raise ExplanationRequiredError(question_key)

    now = datetime.now(UTC)
    row = session.execute(
        select(CredentialDisclosureRow).where(
            and_(
                CredentialDisclosureRow.user_id == user_id,
                CredentialDisclosureRow.question_key == question_key,
                CredentialDisclosureRow.question_version == question_version,
            )
        )
    ).scalar_one_or_none()

    if row is None:
        row = CredentialDisclosureRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            question_key=question_key,
            question_version=question_version,
            answer=answer,
            explanation=explanation,
            answered_at=answered_at or now,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.answer = answer
        row.explanation = explanation
        row.answered_at = answered_at or now
        row.updated_at = now

    session.flush()
    return row


def current_answers(session: Session, user_id: str) -> dict[str, CredentialDisclosureRow]:
    """The latest-version answer for each question key.

    "Latest" is the highest ``question_version``, not the most recently
    answered: a backfill that records an old version's answer today must not
    displace the current one. What an application pre-fill reads.
    """
    rows = (
        session.execute(
            select(CredentialDisclosureRow)
            .where(CredentialDisclosureRow.user_id == user_id)
            .order_by(
                CredentialDisclosureRow.question_key,
                CredentialDisclosureRow.question_version,
            )
        )
        .scalars()
        .all()
    )
    # Ordered ascending by version, so the last write per key wins.
    return {row.question_key: row for row in rows}


def history(session: Session, user_id: str, question_key: str) -> list[CredentialDisclosureRow]:
    """Every version of one question she has answered, oldest wording first."""
    return list(
        session.execute(
            select(CredentialDisclosureRow)
            .where(
                and_(
                    CredentialDisclosureRow.user_id == user_id,
                    CredentialDisclosureRow.question_key == question_key,
                )
            )
            .order_by(CredentialDisclosureRow.question_version)
        )
        .scalars()
        .all()
    )
