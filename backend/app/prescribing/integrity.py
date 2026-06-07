# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tamper-evident integrity for prescribing encounters.

Three primitives backing the defensibility guarantees of a prescribing
encounter (the integrity section of a Clinical Decision Summary):

* **Content digest** — a deterministic SHA-256 over a canonical
  serialization of a record snapshot. Re-hashing the stored snapshot and
  comparing detects any after-the-fact edit.
* **Hash chain** — each entry links to the previous digest, so the
  finalized encounter plus its addenda form an append-only chain: altering
  or dropping any link breaks every digest after it.
* **Immutability rule** — a finalized (or voided) encounter is frozen;
  corrections are dated, labelled addenda appended to the chain, never
  edits to the closed record.

Pure and dependency-free: no database, no ORM, no clock. Timestamps are
supplied by the caller from the server clock (contemporaneous capture — the
persistence layer never trusts a client-supplied time, so backdating isn't
representable).
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

# Only an open encounter may be edited in place. Mirrors
# ``ENCOUNTER_STATUSES`` in ``app.db.models``; kept as a local literal so
# this module stays free of the ORM import.
_MUTABLE_STATUSES = frozenset({"open"})


class EncounterImmutableError(RuntimeError):
    """Raised on an attempt to edit a finalized/voided encounter in place."""


def content_digest(snapshot: Mapping[str, Any]) -> str:
    """Return the SHA-256 hex digest of ``snapshot``.

    The snapshot is serialized canonically — keys sorted, compact
    separators, non-ASCII preserved — so the digest depends only on the
    content, not on dict ordering or incidental whitespace. Values that
    aren't natively JSON-serializable (dates, Decimals) are stringified via
    ``str`` so a caller can hand in an ORM row's field dict directly.
    """

    canonical = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def chain_digest(previous: str | None, entry_digest: str) -> str:
    """Link ``entry_digest`` onto a hash chain rooted at ``previous``.

    ``previous`` is the prior link's chain digest (or ``None`` for the
    genesis link — the encounter's own finalization). The result commits to
    both the new entry and the entire history before it, so removing or
    reordering any earlier link changes every subsequent chain digest.
    """

    return hashlib.sha256(f"{previous or ''}:{entry_digest}".encode()).hexdigest()


def assert_encounter_mutable(status: str) -> None:
    """Raise :class:`EncounterImmutableError` unless ``status`` is editable.

    A finalized or voided encounter is frozen; the only lawful change is a
    dated, labelled addendum. Call this before any in-place write to an
    encounter or its prescriptions.
    """

    if status not in _MUTABLE_STATUSES:
        msg = (
            f"Encounter is {status!r} and immutable; record a dated addendum "
            "instead of editing the closed record."
        )
        raise EncounterImmutableError(msg)
