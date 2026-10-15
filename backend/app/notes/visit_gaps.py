# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether a drafted field says only that the visit did not cover it.

A field the visit never touched still says so, in the words a prescriber
would chart for that item rather than one stock phrase: "Not assessed this
visit." for the ideation screen, "None discussed." for protective factors,
"Not quantified." for a severity, "No collateral information obtained during
this visit." Code that reads a draft (whether a risk field's finding gives way
to the client's quoted words, whether the psychotherapy block was written at
all, whether a history field has anything for the chart) has to tell such a
statement from a finding, so it asks here.

A gap says what was not done: not stated, asked, assessed, discussed. It is
never a denial or a result. "Denies SI.", "No SI reported." and "None." are
findings, and so is anything with a second sentence. The forms recognised:

- "Not <done>" with an optional subject before it: "Not reported.",
  "Severity not quantified.", "Sleep onset not explicitly stated as
  difficult.", "(dose not specified)".
- "None <done>": "None discussed.", "None named."
- "No <item> <done>", where what was not done is a step of the visit (asked,
  discussed, obtained, assessed) and never what the client said: "No
  collateral information obtained during this visit." is a gap; "No SI
  reported." is a finding.
"""

from __future__ import annotations

import re

_DONE = (
    r"(?:stated|reported|discussed|assessed|asked|answered|addressed|quantified|specified|"
    r"reviewed|obtained|elicited|documented|recorded|covered|mentioned|named|described|"
    r"identified|evaluated|checked|screened|explored|given|provided|dictated)"
)
"""What a gap says was not done."""

_STEP = (
    r"(?:discussed|assessed|asked|addressed|reviewed|obtained|elicited|screened|explored|"
    r"evaluated|documented)"
)
"""What "No <item> ..." may say was not done: a step of the visit, never "reported" or
"stated", which make it the client's denial."""

_HEDGE = r"(?:(?:explicitly|specifically|clearly|otherwise|yet|further|formally)\s+)?"

_CLAIM = r"(?:denie[sd]|deny|endorse[sd]|reports?|admits?|states?|said)"
"""A word that makes the statement a finding: what the client said."""

_FINDING_WORD = rf"(?:and|but|or|{_CLAIM})"
"""A word that, before the "not" or after the gap, joins a finding to it."""

_WORD = rf"(?!{_FINDING_WORD}\b)[\w/'-]+"

_ITEM_WORD = rf"(?!{_CLAIM}\b)[\w/'-]+"
"""A word of what "No ..." names, which may be a list ("No goal or assignment")."""

_SUBJECT = rf"(?:{_WORD}\s+){{0,5}}"

_TAIL = rf"(?:\s+{_WORD}){{0,8}}"
"""What may follow ("this visit", "as difficult", "during this visit"): plain words
only, so a comma, a second clause or a finding after the gap is not one."""

_GAP = re.compile(
    rf"^(?:{_SUBJECT}(?:was\s+|were\s+|is\s+|are\s+)?not\s+{_HEDGE}{_DONE}{_TAIL}"
    rf"|none\s+{_DONE}{_TAIL}"
    rf"|no(?:\s+{_ITEM_WORD}){{1,10}}?\s+(?:was\s+|were\s+)?{_STEP}{_TAIL})$",
    re.IGNORECASE,
)

_MAX_LENGTH = 160


def is_visit_gap(text: str) -> bool:
    """Whether ``text`` says only that the visit did not cover the field (the forms above)."""
    stated = text.strip()
    if stated.startswith("(") and stated.endswith(")"):
        stated = stated[1:-1].strip()
    stated = stated.rstrip(".").strip()
    if not stated or len(stated) > _MAX_LENGTH:
        return False
    return _GAP.match(stated) is not None
