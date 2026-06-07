# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Prescribing-encounter logic that sits above the persistence layer.

Currently the integrity primitives (tamper-evident hashing, append-only
addenda chaining, and the immutability rule for finalized encounters) that
back a prescribing encounter's defensibility guarantees. Pure and
dependency-free — no database or ORM.
"""

from __future__ import annotations

from .integrity import (
    EncounterImmutableError,
    assert_encounter_mutable,
    chain_digest,
    content_digest,
)

__all__ = [
    "EncounterImmutableError",
    "assert_encounter_mutable",
    "chain_digest",
    "content_digest",
]
