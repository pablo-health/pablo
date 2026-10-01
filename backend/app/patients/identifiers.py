# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""How an outside identifier is compared, and how it is kept.

A source names a client its own way — a calendar series id, a code like
``SH00001``, a name or initials in a feed title — and a remembered answer is
keyed by that identifier. Two things are settled here for every source:

* **Comparison** ignores case and extra whitespace (:func:`normalize`), so
  the same client typed two ways is one identifier.
* **Storage** keeps a keyed digest, never the identifier itself
  (:func:`identifier_digest`). A feed code is often a client's name in plain
  text, and a plain hash of a name is reversible by anyone holding the
  practice's client list. Keyed under a server-side secret it is not, and
  equality is all matching needs. The kind prefix stays readable —
  ``series:``, ``shape:``, ``feed:`` — because what kind of identifier an
  answer is under decides whether it may book without asking.

An answer belongs to a **scope**: one calendar, for a calendar's series,
since a personal calendar has one follower and a shared calendar's answer is
shared; or one clinician, for a feed's or an export's client codes and
names. Those are not the practice's: a Sessions Health code is numbered from
the clinician's own export, and two clinicians' clients can share a name, so
one clinician's ``SH00001`` or "Jane Smith" says nothing about another's.
"""

from __future__ import annotations

import hashlib
import hmac

_CALENDAR_SCOPE_PREFIX = "calendar:"
#: Spelled out again in ``db.practice_answers``: the row policy matches it.
CLINICIAN_SCOPE_PREFIX = "clinician:"

#: The prefixes an identifier's kind is read from; anything else is a feed's
#: own client identifier.
_KIND_PREFIXES = ("series:", "shape:")
_FEED_KIND = "feed:"

#: What the digest key is derived for, from the configured calendar secret.
_IDENTIFIER_PURPOSE = "patient-source-identifier"


def normalize(value: str | None) -> str:
    """Lower case, with runs of whitespace collapsed and the ends trimmed."""
    return " ".join((value or "").split()).lower()


def calendar_scope(calendar_id: str) -> str:
    """The scope of answers about one calendar's series."""
    return f"{_CALENDAR_SCOPE_PREFIX}{calendar_id}"


def is_calendar_scope(scope: str) -> bool:
    return scope.startswith(_CALENDAR_SCOPE_PREFIX)


def clinician_scope(user_id: str) -> str:
    """The scope of one clinician's answers about their own feed's client codes and names."""
    return f"{CLINICIAN_SCOPE_PREFIX}{user_id}"


def identifier_digest(identifier: str) -> str:
    """The keyed digest an identifier is remembered under: ``<kind>:<hmac>``.

    The digest is of the whole normalised identifier, prefix included, so a
    series id and a feed code that happen to share characters never share a
    digest. Keyed under a subkey of the calendar secret, so a copy of the
    table says nothing to anyone without the key.
    """
    # Lazy: the services package imports the matcher, which imports this.
    from ..services.token_encryption import derive_subkey  # noqa: PLC0415

    normalized = normalize(identifier)
    kind = next((p for p in _KIND_PREFIXES if normalized.startswith(p)), _FEED_KIND)
    key = derive_subkey(_IDENTIFIER_PURPOSE)
    return kind + hmac.new(key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


__all__ = [
    "CLINICIAN_SCOPE_PREFIX",
    "calendar_scope",
    "clinician_scope",
    "identifier_digest",
    "is_calendar_scope",
    "normalize",
]
