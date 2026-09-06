# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Shared refuse-and-list validation for claim-adjacent surfaces.

A claim (or a superbill standing in for one) either has everything a payer
requires or it doesn't — there's no partial submission. Rather than each of
the surfaces that assemble one (superbills, exports, claim assembly)
re-deriving its own idea of "what's missing", they all call
:func:`missing_fields` and refuse with the same list a person can act on.
"""

from __future__ import annotations

from typing import Any


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
