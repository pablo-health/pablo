# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Prescribing-encounter logic that sits above the persistence layer.

Two layers:

* :mod:`.integrity` — the tamper-evident primitives (content hashing,
  append-only addenda chaining, the immutability rule for finalized
  encounters). Pure and dependency-free — no database or ORM.
* :mod:`.attestation` — the verification ledger ("no checkbox without
  evidence"): runs the pure rules engine against an encounter and persists one
  ``prescribing_checklist_items`` row per applicable item. This layer touches
  the ORM session.
* :mod:`.finalization` — freezing + signing an encounter and appending
  tamper-evident addenda, plus the verification helpers that recompute the
  integrity digests. This layer touches the ORM session.
"""

from __future__ import annotations

from .attestation import (
    bind_evidence,
    build_facts,
    build_rule_context,
    live_checklist,
    sync_checklist,
)
from .finalization import (
    EncounterNotFinalizedError,
    FinalizationBlockedError,
    MissingAttestationError,
    append_addendum,
    encounter_snapshot,
    finalize_encounter,
    live_addenda,
    set_clinical_reasoning,
    verify_addenda_chain,
    verify_encounter_integrity,
)
from .integrity import (
    EncounterImmutableError,
    assert_encounter_mutable,
    chain_digest,
    content_digest,
)

__all__ = [
    "EncounterImmutableError",
    "EncounterNotFinalizedError",
    "FinalizationBlockedError",
    "MissingAttestationError",
    "append_addendum",
    "assert_encounter_mutable",
    "bind_evidence",
    "build_facts",
    "build_rule_context",
    "chain_digest",
    "content_digest",
    "encounter_snapshot",
    "finalize_encounter",
    "live_addenda",
    "live_checklist",
    "set_clinical_reasoning",
    "sync_checklist",
    "verify_addenda_chain",
    "verify_encounter_integrity",
]
