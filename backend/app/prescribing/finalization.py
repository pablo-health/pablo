# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Finalize a prescribing encounter and append tamper-evident addenda.

This is the write side of the defensibility record that the integrity
primitives (:mod:`app.prescribing.integrity`) back and the Clinical Decision
Summary reads:

* :func:`finalize_encounter` freezes an open encounter — it refuses while any
  applicable hard-stop ledger item is still missing (the engine's
  ``can_finalize`` rule, read off the persisted ledger), records the
  prescriber's signature + attestation statement, computes the
  ``integrity_digest`` over a canonical snapshot of the encounter + its
  prescriptions + the ledger (the genesis link of the hash chain), and flips
  the status to ``finalized``.
* :func:`append_addendum` is the only lawful change to a finalized encounter:
  a dated, labelled correction in the clinician's own words, chained onto the
  prior link so any later edit or reorder breaks every digest after it.
* :func:`verify_addenda_chain` / :func:`verify_encounter_integrity` recompute
  the digests so the summary can show whether the record is intact.

These operate on already-loaded, tenant-scoped rows; the caller owns loading
them under a tenant-scoped session and owns the transaction. The session is
flushed but not committed here.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ..db.models import PrescribingEncounterAddendumRow
from ..rules.enforcement import FlagBehavior, ItemStatus
from .integrity import assert_encounter_mutable, chain_digest, content_digest

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.orm import Session

    from ..db.models import (
        PrescribingChecklistItemRow,
        PrescribingEncounterRow,
        PrescriptionRow,
    )

_FINALIZED = "finalized"


class FinalizationBlockedError(RuntimeError):
    """Finalization refused: applicable hard-stop items are still missing.

    Carries the offending ``item_ids`` so the caller can surface exactly what
    blocks the signature. A hard stop is the engine's strongest flag — the
    record cannot be signed until each is satisfied (or the prescription
    changes so the item no longer applies).
    """

    def __init__(self, item_ids: list[str]) -> None:
        self.item_ids = item_ids
        joined = ", ".join(item_ids)
        super().__init__(f"Cannot finalize: hard-stop items still missing: {joined}.")


class MissingAttestationError(ValueError):
    """Finalization refused: the prescriber's attestation statement is empty."""


class EncounterNotFinalizedError(RuntimeError):
    """An addendum was attempted on an encounter that is not finalized.

    Addenda are corrections to a *closed* record; an open encounter is edited
    in place, and a voided one takes no further entries.
    """

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(
            f"Encounter is {status!r}; addenda may only be appended to a finalized encounter."
        )


def _prescription_snapshot(rx: PrescriptionRow) -> dict[str, Any]:
    return {
        "id": rx.id,
        "rxnorm_id": rx.rxnorm_id,
        "drug_name": rx.drug_name,
        "schedule": rx.schedule,
        "drug_class": rx.drug_class,
        "strength": rx.strength,
        "quantity": rx.quantity,
        "days_supply": rx.days_supply,
        "refills": rx.refills,
        "indication": rx.indication,
        "first_in_course": rx.first_in_course,
    }


def _checklist_snapshot(item: PrescribingChecklistItemRow) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "requirement_level": item.requirement_level,
        "flag_behavior": item.flag_behavior,
        "status": item.status,
        "authority_ref": item.authority_ref,
        "evidence_link": item.evidence_link,
        "ruleset_version": item.ruleset_version,
    }


def encounter_snapshot(
    encounter: PrescribingEncounterRow,
    prescriptions: Sequence[PrescriptionRow],
    checklist: Sequence[PrescribingChecklistItemRow],
) -> dict[str, Any]:
    """The canonical snapshot the ``integrity_digest`` commits to.

    Covers the encounter's defensibility-relevant fields (including the
    signature + attestation statement), every prescription, and the full
    ledger — each list sorted by a stable key so the digest depends only on
    content, not row order.
    """

    return {
        "encounter": {
            "id": encounter.id,
            "patient_id": encounter.patient_id,
            "prescriber_user_id": encounter.prescriber_user_id,
            "prescriber_type": encounter.prescriber_type,
            "prescriber_npi": encounter.prescriber_npi,
            "prescriber_dea": encounter.prescriber_dea,
            "prescriber_license": encounter.prescriber_license,
            "delegation_ref": encounter.delegation_ref,
            "delegating_physician_name": encounter.delegating_physician_name,
            "delegating_physician_dea": encounter.delegating_physician_dea,
            "state": encounter.state,
            "modality": encounter.modality,
            "prior_in_person": encounter.prior_in_person,
            "patient_in_sud_program": encounter.patient_in_sud_program,
            "ruleset_version": encounter.ruleset_version,
            "clinical_reasoning": encounter.clinical_reasoning,
            "encountered_at": encounter.encountered_at,
            "finalized_at": encounter.finalized_at,
            "finalized_by": encounter.finalized_by,
            "attestation_statement": encounter.attestation_statement,
        },
        "prescriptions": [
            _prescription_snapshot(r) for r in sorted(prescriptions, key=lambda r: r.id)
        ],
        "checklist": [_checklist_snapshot(i) for i in sorted(checklist, key=lambda i: i.item_id)],
    }


def _blocking_item_ids(checklist: Sequence[PrescribingChecklistItemRow]) -> list[str]:
    """Item ids of applicable hard-stop items that are still missing."""
    return [
        item.item_id
        for item in checklist
        if item.status == ItemStatus.MISSING.value
        and item.flag_behavior == FlagBehavior.HARD_STOP.value
    ]


def set_clinical_reasoning(
    session: Session,
    encounter: PrescribingEncounterRow,
    reasoning: str | None,
    *,
    now: datetime,
) -> PrescribingEncounterRow:
    """Record the prescriber's clinical reasoning on an open encounter.

    The reasoning is the clinician's own free text (§1 of the Clinical Decision
    Summary) — the system never machine-populates it, and it is frozen into the
    integrity digest at finalization. Editable only while the encounter is
    open; raises
    :class:`~app.prescribing.integrity.EncounterImmutableError` on a closed
    encounter. Passing ``None`` or blank clears it.
    """

    assert_encounter_mutable(encounter.status)
    cleaned = (reasoning or "").strip()
    encounter.clinical_reasoning = cleaned or None
    encounter.updated_at = now
    session.flush()
    return encounter


def finalize_encounter(  # noqa: PLR0913 — service deps + keyword-only audit fields
    session: Session,
    encounter: PrescribingEncounterRow,
    prescriptions: Sequence[PrescriptionRow],
    checklist: Sequence[PrescribingChecklistItemRow],
    *,
    signed_by: str,
    attestation_statement: str,
    now: datetime,
) -> PrescribingEncounterRow:
    """Freeze + sign an open encounter, stamping the genesis integrity digest.

    Refuses (:class:`FinalizationBlockedError`) while any applicable hard-stop
    ledger item is missing, and (:class:`MissingAttestationError`) without an
    attestation statement. On success records the signature
    (``finalized_by`` + ``attestation_statement`` + ``finalized_at``), computes
    ``integrity_digest`` over the canonical snapshot, and sets status to
    ``finalized``. Raises
    :class:`~app.prescribing.integrity.EncounterImmutableError` if the
    encounter is already closed.
    """

    assert_encounter_mutable(encounter.status)

    statement = (attestation_statement or "").strip()
    if not statement:
        raise MissingAttestationError("An attestation statement is required to finalize.")

    blocking = _blocking_item_ids(checklist)
    if blocking:
        raise FinalizationBlockedError(blocking)

    encounter.finalized_by = signed_by
    encounter.attestation_statement = statement
    encounter.finalized_at = now
    encounter.status = _FINALIZED
    encounter.updated_at = now
    # Digest over the now-signed snapshot — the genesis link of the chain.
    encounter.integrity_digest = content_digest(
        encounter_snapshot(encounter, prescriptions, checklist)
    )

    session.flush()
    return encounter


def _addendum_content(
    encounter_id: str, *, label: str, text: str, author: str, created_at: datetime
) -> dict[str, Any]:
    return {
        "encounter_id": encounter_id,
        "label": label,
        "text": text,
        "created_by": author,
        "created_at": created_at,
    }


def live_addenda(
    session: Session,
    encounter_id: str,
) -> list[PrescribingEncounterAddendumRow]:
    """Return an encounter's addenda in chain order (oldest first)."""
    return list(
        session.scalars(
            select(PrescribingEncounterAddendumRow)
            .where(PrescribingEncounterAddendumRow.encounter_id == encounter_id)
            .order_by(
                PrescribingEncounterAddendumRow.created_at,
                PrescribingEncounterAddendumRow.id,
            )
        ).all()
    )


def append_addendum(  # noqa: PLR0913 — service deps + keyword-only audit fields
    session: Session,
    encounter: PrescribingEncounterRow,
    *,
    label: str,
    text: str,
    author: str,
    now: datetime,
) -> PrescribingEncounterAddendumRow:
    """Append a dated, labelled correction to a finalized encounter's hash chain.

    The new link's ``prev_digest`` is the prior chain link — the last
    addendum's ``digest``, or the encounter's ``integrity_digest`` for the
    first addendum — and ``digest`` chains the addendum's content onto it, so
    removing or reordering any addendum breaks every digest after it. Raises
    :class:`EncounterNotFinalizedError` unless the encounter is finalized, and
    ``ValueError`` if the label or text is empty.
    """

    if encounter.status != _FINALIZED:
        raise EncounterNotFinalizedError(encounter.status)

    label_s = (label or "").strip()
    text_s = (text or "").strip()
    if not label_s or not text_s:
        raise ValueError("An addendum requires both a label and text.")

    existing = live_addenda(session, encounter.id)
    prev = existing[-1].digest if existing else encounter.integrity_digest
    entry_digest = content_digest(
        _addendum_content(encounter.id, label=label_s, text=text_s, author=author, created_at=now)
    )
    chain = chain_digest(prev, entry_digest)

    row = PrescribingEncounterAddendumRow(
        id=str(uuid.uuid4()),
        encounter_id=encounter.id,
        patient_id=encounter.patient_id,
        label=label_s,
        text=text_s,
        digest=chain,
        prev_digest=prev,
        created_by=author,
        created_at=now,
    )
    session.add(row)
    session.flush()
    return row


def verify_addenda_chain(
    encounter: PrescribingEncounterRow,
    addenda: Sequence[PrescribingEncounterAddendumRow],
) -> bool:
    """Recompute the addendum hash chain and confirm every link is intact.

    ``addenda`` must be in chain order (oldest first — as :func:`live_addenda`
    returns them). Returns ``True`` only when each link's ``prev_digest``
    matches the prior link and its ``digest`` is the recomputed chain digest of
    its content — so any after-the-fact edit, removal, or reorder is detected.
    An encounter with no addenda is trivially intact.
    """

    prev = encounter.integrity_digest
    for entry in addenda:
        if entry.prev_digest != prev:
            return False
        entry_digest = content_digest(
            _addendum_content(
                entry.encounter_id,
                label=entry.label,
                text=entry.text,
                author=entry.created_by,
                created_at=entry.created_at,
            )
        )
        if entry.digest != chain_digest(prev, entry_digest):
            return False
        prev = entry.digest
    return True


def verify_encounter_integrity(
    encounter: PrescribingEncounterRow,
    prescriptions: Sequence[PrescriptionRow],
    checklist: Sequence[PrescribingChecklistItemRow],
) -> bool:
    """Recompute the encounter digest and confirm it matches what was stored.

    Returns ``False`` for an un-finalized encounter (no digest to verify) or
    when the recomputed snapshot digest differs from ``integrity_digest`` —
    i.e. the finalized record was altered after signing.
    """

    if not encounter.integrity_digest:
        return False
    recomputed = content_digest(encounter_snapshot(encounter, prescriptions, checklist))
    return recomputed == encounter.integrity_digest
