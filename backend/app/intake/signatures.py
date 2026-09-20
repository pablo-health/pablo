# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a signature records, reduced to one string.

A signature row carries a dozen columns describing one moment: which text,
which person, which role, which session, when, and how strongly they had
proved who they were. :func:`evidence_digest` folds exactly those columns
into a sha256 so that the record can be checked against itself later —
recompute the digest from the row, compare it with the digest the row
stores, and a column that has been edited since shows up as a mismatch.

**It is a consistency check, not a seal.** Anybody who can rewrite the row
can rewrite the digest beside it, and this makes no claim otherwise. What
it catches is the realistic failure: a column changed by a migration, a
backfill, a repair script or a bug, with nothing else in the record looking
wrong. The append-only audit log is the separate record of the event, and
the document's own digest is what names the words that were read.

**The field list is the contract.** :data:`EVIDENCE_FIELDS` is what the
digest covers, in one place, because the digest is only meaningful if both
sides of the comparison agree on it. Adding a column to the table does NOT
add it here: an existing row's digest was taken over the old list, so
widening the list would make every stored digest fail to recompute. A new
field means a new consent statement version and a new set of rows, or it
means the field is not evidence.

The encoding is pinned for the same reason the document's canonical text
is: sorted keys, no insignificant whitespace, UTF-8, and every value
written as a string or as ``null``. Numbers and datetimes never reach the
JSON encoder as themselves, so no future change to how Python renders one
can move a digest that was taken years ago.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

#: The columns the digest is taken over, in no particular order — the
#: encoder sorts them. Append-only in the same sense as the consent
#: statements: see the module docstring for why widening it is not a
#: cosmetic change.
EVIDENCE_FIELDS: tuple[str, ...] = (
    "assignment_id",
    "auth_strength",
    "consent_statement_version",
    "document_digest",
    "document_version_id",
    "ip",
    "item_id",
    "patient_id",
    "session_id",
    "signed_at",
    "signer_role",
    "signer_typed_name",
    "user_agent",
)


def evidence_of(row: Mapping[str, object]) -> dict[str, str | None]:
    """The evidence fields of a stored signature, normalised for hashing.

    Every value becomes a string or ``None``: a ``datetime`` through
    ``isoformat``, everything else through ``str``. A field the row does not
    carry reads as ``None`` rather than raising, so a row read back from a
    repository that omits an optional column digests the same as one that
    stored it as NULL.
    """
    return {field: _as_text(row.get(field)) for field in EVIDENCE_FIELDS}


def evidence_digest(row: Mapping[str, object]) -> str:
    """The sha256 of a signature's evidence, lowercase hex.

    Takes the whole row and reads the fields it needs, so a caller never has
    to assemble the payload itself — which is what keeps the value written
    at signing time and the value recomputed at audit time comparable.
    """
    payload = json.dumps(
        evidence_of(row),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _as_text(value: object) -> str | None:
    """One value as the digest sees it: a string, or ``None``."""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


__all__ = ["EVIDENCE_FIELDS", "evidence_digest", "evidence_of"]
