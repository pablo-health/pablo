# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Shared refuse-and-list validation for claim-adjacent surfaces.

A claim (or a superbill standing in for one) either has everything a payer
requires or it doesn't — there's no partial submission. Rather than each of
the surfaces that assemble one (superbills, exports, claim assembly)
re-deriving its own idea of "what's missing", they all call
:func:`missing_fields` and refuse with the same list a person can act on.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

#: An ICD-10-CM code: a letter (never U), a digit, an alphanumeric, then an
#: optional dot and up to four more alphanumerics. Matches with or without the
#: dot so a code can be checked in either the human form (``F41.1``) or the
#: claim wire form (``F411``).
_ICD10_CM_CODE = re.compile(r"^[A-TV-Z][0-9][0-9A-Z](?:\.?[0-9A-Z]{1,4})?$")

#: The bare-category length: three characters. A category alone is the
#: clearinghouse's "not to the highest level of specificity" rejection.
_ICD10_CM_CATEGORY_LENGTH = 3

#: An 837P service line carries at most four diagnosis pointers (SV107).
MAX_DX_POINTERS_PER_LINE = 4


def dx_at_highest_specificity(code: str) -> bool:
    """Is ``code`` a well-formed ICD-10-CM code carried past its bare category?

    A payer edit rejects a diagnosis submitted as a three-character category
    (``F41``) when the code set subdivides it (``F41.1``, ``F41.9``); this
    is the local pre-flight for that edit. Without the code table on hand it
    can only reject a bare category or a malformed code — it does not know
    whether ``F41.1`` itself has a fifth character in the current code set —
    so a ``True`` here means "not obviously incomplete", not "billable".
    The handful of three-character codes that are billable on their own are
    rejected too; a caller that needs them can carry an allow-list.
    """
    normalized = code.strip().upper()
    if not _ICD10_CM_CODE.match(normalized):
        return False
    return len(normalized.replace(".", "")) > _ICD10_CM_CATEGORY_LENGTH


def dx_pointers_valid(pointers: Sequence[str | int], n_dx: int) -> bool:
    """Do a service line's diagnosis pointers each name a diagnosis on the claim?

    Pointers are 1-based positions into the claim's diagnosis list, so every
    one must fall in ``1..n_dx``. A line needs at least one and carries at
    most four; a repeated pointer is a defect too (the same diagnosis cannot
    justify a line twice).
    """
    if not 1 <= len(pointers) <= MAX_DX_POINTERS_PER_LINE:
        return False
    positions: list[int] = []
    for pointer in pointers:
        try:
            position = int(str(pointer).strip())
        except ValueError:
            return False
        positions.append(position)
    return all(1 <= p <= n_dx for p in positions) and len(set(positions)) == len(positions)


def missing_fields(obj: Any, required: list[str]) -> list[str]:
    """Names in ``required`` that are absent, ``None``, or blank on ``obj``.

    ``obj`` may be a mapping or any object exposing the names as attributes
    (a dataclass, an ORM row, a Pydantic model). A blank string (empty or
    whitespace-only) counts as missing — a form field a person tabbed
    through without typing anything is not a filled-in value.
    """
    missing: list[str] = []
    for field in required:
        value = obj.get(field) if isinstance(obj, dict) else getattr(obj, field, None)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            missing.append(field)
    return missing
