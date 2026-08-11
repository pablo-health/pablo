# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The attestation ledger — "no checkbox without evidence".

This binds the pure rules engine (:mod:`app.rules.enforcement`) to the
per-tenant ``prescribing_checklist_items`` table. For an open prescribing
encounter it:

* assembles the rules-engine :class:`~app.rules.models.RuleContext` and the
  flat ``facts`` mapping from the encounter + prescription columns (the field
  paths the curated ruleset ``trigger`` / ``satisfied_when`` predicates read),
* runs :func:`~app.rules.enforcement.evaluate_enforcement` with the evidence
  already bound on the ledger, and
* upserts one ledger row per *applicable* item, recording the engine's
  computed status, the flag behavior / requirement level, the authority
  citation, and the ruleset version in force.

The engine is the single source of truth for an item's *status*: this module
never invents a ``satisfied`` — an item is satisfied only when its evidence
resolves (a bound ``evidence_link``) or its computed ``satisfied_when`` check
holds, exactly as the evaluator decides. Evidence binding
(:func:`bind_evidence`) records *that* a clinician attached a record; the next
:func:`sync_checklist` lets the engine re-derive the status from it. (Verifying
that an ``evidence_link`` points at a real, on-chart record — rather than
trusting its presence — is the Phase-3 chart-review accelerator, not this
manual-binding layer.)

The ledger is mutable only while the encounter is ``open``; both
:func:`sync_checklist` and :func:`bind_evidence` refuse a finalized or voided
encounter via :func:`~app.prescribing.integrity.assert_encounter_mutable`,
since corrections to a closed record are dated addenda, not edits.

The ruleset is passed in by the caller — a downstream deployment's overlay
decides which state ruleset applies. This module — OSS — never reaches into
the overlay to fetch it.

Scope note: one evaluation covers one (encounter, prescription) pair, matching
the engine's single-context shape and the singular ``prescription.*`` fact
paths. An encounter with several controlled prescriptions is synced one
prescription at a time by the caller; cross-prescription aggregation is a
later concern.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ..db.models import PrescribingChecklistItemRow
from ..rules.enforcement import evaluate_enforcement
from ..rules.models import RuleContext
from .integrity import assert_encounter_mutable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.orm import Session

    from ..db.models import PrescribingEncounterRow, PrescriptionRow
    from ..rules.models import Ruleset


def build_rule_context(
    encounter: PrescribingEncounterRow,
    prescription: PrescriptionRow,
) -> RuleContext:
    """Build the layer-1 applicability context from the encounter + prescription.

    Applicability is selected by the prescriber type, the state, and the
    prescription's schedule + drug class — a non-controlled drug
    (``schedule == "none"``) selects nothing.
    """

    return RuleContext(
        provider_type=encounter.prescriber_type,
        state=encounter.state,
        schedule=prescription.schedule,
        drug_class=prescription.drug_class,
    )


def build_facts(
    encounter: PrescribingEncounterRow,
    prescription: PrescriptionRow,
) -> dict[str, Any]:
    """Build the flat ``facts`` mapping the trigger / satisfied_when DSL reads.

    The dotted field paths mirror the contract documented on
    :class:`~app.db.models.PrescribingEncounterRow`: ``prescription.*`` from
    the prescription, ``context.*`` from the encounter (plus the
    conditional-trigger facts that live on the prescription), and
    ``prescriber.*`` from the snapshotted prescriber columns.
    ``prescriber.delegation_status`` is ``"delegated"`` exactly when a
    delegation agreement is referenced.
    """

    return {
        "prescription.schedule": prescription.schedule,
        "prescription.drug_class": prescription.drug_class,
        "prescription.days_supply": prescription.days_supply,
        "prescription.refills": prescription.refills,
        "prescription.quantity": prescription.quantity,
        "prescription.strength": prescription.strength,
        "context.state": encounter.state,
        "context.modality": encounter.modality,
        "context.prior_in_person": encounter.prior_in_person,
        "context.patient_in_sud_program": encounter.patient_in_sud_program,
        "context.indication": prescription.indication,
        "context.first_in_course": prescription.first_in_course,
        "prescriber.type": encounter.prescriber_type,
        "prescriber.dea": encounter.prescriber_dea,
        "prescriber.license": encounter.prescriber_license,
        "prescriber.npi": encounter.prescriber_npi,
        "prescriber.delegation_status": ("delegated" if encounter.delegation_ref else None),
    }


def _existing_rows(
    session: Session,
    encounter_id: str,
) -> dict[str, PrescribingChecklistItemRow]:
    """Return the live (non-deleted) ledger rows for an encounter, by item id."""

    rows = session.scalars(
        select(PrescribingChecklistItemRow).where(
            PrescribingChecklistItemRow.encounter_id == encounter_id,
            PrescribingChecklistItemRow.deleted_at.is_(None),
        )
    ).all()
    return {row.item_id: row for row in rows}


def sync_checklist(  # noqa: PLR0913 — service deps + keyword-only audit fields
    session: Session,
    encounter: PrescribingEncounterRow,
    prescription: PrescriptionRow,
    ruleset: Ruleset,
    *,
    actor: str,
    now: datetime,
) -> list[PrescribingChecklistItemRow]:
    """Recompute the attestation ledger for an open encounter + prescription.

    Runs the enforcement evaluator with the evidence already bound on the
    ledger, then upserts one row per applicable item (preserving any bound
    evidence) and soft-deletes rows whose item no longer applies. Stamps the
    encounter with the ruleset version in force. Returns the live ledger rows
    in ruleset order. The session is flushed but not committed — the caller
    owns the transaction.

    Raises :class:`~app.prescribing.integrity.EncounterImmutableError` if the
    encounter is finalized or voided.
    """

    assert_encounter_mutable(encounter.status)

    existing = _existing_rows(session, encounter.id)
    evidence = {
        item_id: row.evidence_link
        for item_id, row in existing.items()
        if row.evidence_link is not None
    }

    context = build_rule_context(encounter, prescription)
    facts = build_facts(encounter, prescription)
    report = evaluate_enforcement(ruleset, context, facts, evidence)

    applicable_ids: set[str] = set()
    result: list[PrescribingChecklistItemRow] = []

    for evaluation in report.items:
        applicable_ids.add(evaluation.item_id)
        row = existing.get(evaluation.item_id)
        if row is None:
            row = PrescribingChecklistItemRow(
                id=str(uuid.uuid4()),
                encounter_id=encounter.id,
                patient_id=encounter.patient_id,
                item_id=evaluation.item_id,
                created_by=actor,
                created_at=now,
            )
            session.add(row)
        row.requirement_level = evaluation.requirement_level.value
        row.flag_behavior = evaluation.flag_behavior.value
        row.status = evaluation.status.value
        row.authority_ref = evaluation.authority_ref
        row.ruleset_version = ruleset.version
        row.updated_at = now
        result.append(row)

    # Items that stopped applying (the prescription changed) are soft-deleted,
    # never silently flipped — the audit trail keeps what was once tracked.
    for item_id, row in existing.items():
        if item_id not in applicable_ids:
            row.deleted_at = now
            row.updated_at = now

    encounter.ruleset_version = ruleset.version
    encounter.updated_at = now

    session.flush()
    return result


def bind_evidence(  # noqa: PLR0913 — service deps + keyword-only audit fields
    session: Session,
    encounter: PrescribingEncounterRow,
    item_id: str,
    evidence_link: str,
    *,
    actor: str,
    now: datetime,
) -> PrescribingChecklistItemRow:
    """Attach an evidence link to a ledger item on an open encounter.

    Records *that* a clinician bound a record to the item (the link, who, and
    when — server clock, no backdating). The item's ``status`` is not flipped
    here: re-run :func:`sync_checklist` so the engine re-derives the status
    from the new evidence. Raises ``LookupError`` if the item is not on the
    encounter's live ledger (run :func:`sync_checklist` first), and
    :class:`~app.prescribing.integrity.EncounterImmutableError` if the
    encounter is closed.
    """

    assert_encounter_mutable(encounter.status)

    row = session.scalars(
        select(PrescribingChecklistItemRow).where(
            PrescribingChecklistItemRow.encounter_id == encounter.id,
            PrescribingChecklistItemRow.item_id == item_id,
            PrescribingChecklistItemRow.deleted_at.is_(None),
        )
    ).one_or_none()
    if row is None:
        msg = (
            f"No live checklist item {item_id!r} on encounter {encounter.id}; "
            "run sync_checklist first."
        )
        raise LookupError(msg)

    row.evidence_link = evidence_link
    row.captured_by = actor
    row.captured_at = now
    row.updated_at = now

    session.flush()
    return row


def live_checklist(
    session: Session,
    encounter_id: str,
) -> Sequence[PrescribingChecklistItemRow]:
    """Return the live (non-deleted) ledger rows for an encounter."""

    return list(_existing_rows(session, encounter_id).values())
