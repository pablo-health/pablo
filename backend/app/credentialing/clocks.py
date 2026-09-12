# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Compliance due dates derived from record dates — proposed, never written.

``compliance_items`` used to be hand-fed: a clinician typed a licence expiry
into a reminder and typed it again wherever else it was needed. Now the licence
row holds the date and the clock can be computed from it.

The computation only ever PROPOSES. :func:`propose` returns what it would
change and changes nothing; :func:`apply_proposal` writes one proposal the
clinician picked. That split is the point of the module, for two reasons:

* A date the clinician did not enter must not silently become the thing that
  decides whether she gets warned. If the record is wrong, a silent write turns
  one bad date into a missed renewal and a reminder that never fired.
* The record's date and the board's renewal deadline are not always the same
  day, and only she knows when they differ.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, select

from ..db.models import (
    ComplianceItemRow,
    CredentialLiabilityPolicyRow,
    CredentialLicenseRow,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


#: Licence statuses whose expiry is worth warning about. A revoked or suspended
#: licence has a problem that a renewal reminder is the wrong response to.
_LIVE_LICENSE_STATUSES: frozenset[str] = frozenset({"active", "inactive"})


@dataclass(frozen=True)
class ClockProposal:
    """One suggested change to a compliance item's due date.

    ``item_id`` is NULL when no item of that type exists yet, in which case
    applying the proposal creates one. ``current_due_date`` is what the item
    says today, so a caller can show "you have 30 June, the record says 31
    July" rather than just asserting the new value.
    """

    item_type: str
    label: str
    proposed_due_date: date
    source_table: str
    source_id: str
    item_id: str | None = None
    current_due_date: date | None = None

    @property
    def is_change(self) -> bool:
        """Would applying this actually alter anything?"""
        return self.current_due_date != self.proposed_due_date


def propose(session: Session, user_id: str) -> list[ClockProposal]:
    """Every compliance due date the credential record disagrees with.

    Writes nothing. Returns only genuine changes — an item whose due date
    already matches the record produces no proposal, so an empty list means
    "the clocks agree with the facts" and a caller can say so.

    Where a clock is single-instance but the record is not — a clinician holds
    four licences and ``compliance_items`` tracks one ``license`` clock — the
    proposal takes the SOONEST expiry among the live licences. That is the date
    that matters: the first lapse is the one that stops her practising, and
    warning about the latest would warn after the fact.
    """
    proposals: list[ClockProposal] = []

    license_row = session.execute(
        select(CredentialLicenseRow)
        .where(
            and_(
                CredentialLicenseRow.user_id == user_id,
                CredentialLicenseRow.expiration_date.is_not(None),
                CredentialLicenseRow.status.in_(sorted(_LIVE_LICENSE_STATUSES)),
            )
        )
        .order_by(CredentialLicenseRow.expiration_date, CredentialLicenseRow.id)
        .limit(1)
    ).scalar_one_or_none()
    if license_row is not None and license_row.expiration_date is not None:
        proposals.append(
            _proposal_for(
                session,
                user_id,
                item_type="license",
                label="Professional license",
                due=license_row.expiration_date,
                source_table="credential_licenses",
                source_id=license_row.id,
            )
        )

    policy_row = session.execute(
        select(CredentialLiabilityPolicyRow)
        .where(
            and_(
                CredentialLiabilityPolicyRow.user_id == user_id,
                CredentialLiabilityPolicyRow.is_current.is_(True),
                CredentialLiabilityPolicyRow.expiration_date.is_not(None),
            )
        )
        .order_by(CredentialLiabilityPolicyRow.expiration_date, CredentialLiabilityPolicyRow.id)
        .limit(1)
    ).scalar_one_or_none()
    if policy_row is not None and policy_row.expiration_date is not None:
        proposals.append(
            _proposal_for(
                session,
                user_id,
                item_type="liability_insurance",
                label="Malpractice / liability insurance",
                due=policy_row.expiration_date,
                source_table="credential_liability_policies",
                source_id=policy_row.id,
            )
        )

    return [p for p in proposals if p.is_change]


def apply_proposal(session: Session, user_id: str, proposal: ClockProposal) -> ComplianceItemRow:
    """Write one proposal the clinician accepted. Does not commit.

    Takes the proposal rather than an item id and a date so that what gets
    written is exactly what was shown — a caller cannot accept a proposal and
    then apply a different date by reconstructing the arguments.

    ``user_id`` is checked against the item rather than trusted from the
    proposal: the proposal is a value object that may have crossed a request
    boundary, and an item belonging to someone else must not be writable
    because its id was guessed.
    """
    now = datetime.now(UTC)
    if proposal.item_id is None:
        row = ComplianceItemRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            item_type=proposal.item_type,
            label=proposal.label,
            due_date=proposal.proposed_due_date,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row

    existing = session.get(ComplianceItemRow, proposal.item_id)
    if existing is None or existing.user_id != user_id:
        msg = f"No compliance item {proposal.item_id!r} for this clinician"
        raise ValueError(msg)
    existing.due_date = proposal.proposed_due_date
    existing.updated_at = now
    session.flush()
    return existing


def _proposal_for(  # noqa: PLR0913 — keyword-only proposal fields, not a call-site burden
    session: Session,
    user_id: str,
    *,
    item_type: str,
    label: str,
    due: date,
    source_table: str,
    source_id: str,
) -> ClockProposal:
    """Pair a derived date with the existing item of that type, if any."""
    item = session.execute(
        select(ComplianceItemRow)
        .where(
            and_(
                ComplianceItemRow.user_id == user_id,
                ComplianceItemRow.item_type == item_type,
                ComplianceItemRow.completed_at.is_(None),
            )
        )
        .order_by(ComplianceItemRow.created_at, ComplianceItemRow.id)
        .limit(1)
    ).scalar_one_or_none()
    return ClockProposal(
        item_type=item_type,
        label=label,
        proposed_due_date=due,
        source_table=source_table,
        source_id=source_id,
        item_id=item.id if item is not None else None,
        current_due_date=item.due_date if item is not None else None,
    )
