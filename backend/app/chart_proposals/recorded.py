# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a note records that the chart does not have yet: how the chart is first filled.

One rule for every note, keyed off the shape of each field's value and never
off the note's type. For each history field (the substance-use baseline
included) the chart has no value for, a note field of the same key that
states something is proposed as the field's value, as the note words it,
"Recorded this visit". The note is the source, read by the clinician at
review; there is no transcript evidence to cite.

A field's value reads one of three ways:

- Nothing to record: empty, "Not asked.", "Not stated.", "Not recorded" or
  "None recorded" alone, or "Not recorded" followed by a mark that states
  nothing ("(not asked this visit)", "(asked this visit: no change)").
- A chart-fed field the chart was empty for: "Not recorded (stated this
  visit: …)". What the mark quotes is recorded; a substance screen marked
  "(stated this visit: denied)" is recorded as "Denies.".
- Anything else is what the visit drafted, recorded as written: a history
  answer, a substance answer, or a substance denial ("Denies.", "Denies
  smoking or vaping.").

So an intake, whose history and substance fields are drafted from the visit,
seeds every field the visit covered, four denials given in one answer as
four baselines; a follow-up, whose fields print the chart, seeds only a
field the chart was empty for and the visit said something about. The
baseline's date is the chart's: the entry is dated when it is accepted at
signing.

Allergies are read the same way, from the note's ``allergies`` field. While
the chart's allergies are not recorded, a statement that is a denial ("no
allergies", "none that I know of") is proposed as no known drug allergies
(NKDA), which sets the allergy record's status and adds no entry. A stated
allergy is not seeded from the note: an entry needs its substance, which
free text does not give without guessing, so the proposal call proposes it
with the substance and the reaction named.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .denials import NKDA, NKDA_TEXT, is_allergy_denial
from .families import ALLERGIES, chart_key_for
from .models import RECORDED_THIS_VISIT, DraftedProposal

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

#: How a chart-fed field reads when the chart has nothing for it.
_EMPTY_CHART = ("not recorded", "none recorded")

#: What a field reads when the visit did not state anything to record.
_NOTHING_STATED = frozenset(
    {"", "not stated", "not asked", "not recorded", "none recorded", "asked — no change"}
)

_STATED_OPEN = "(stated this visit:"

#: A substance screen's denial mark, and the baseline it records.
_DENIED_MARKS = frozenset({"denied", "denies"})
DENIES = "Denies."


def field_text(value: Any) -> str:
    """A note field's text: a list of text one item a line; a structured field has none."""
    if isinstance(value, list):
        return "\n".join(item.strip() for item in value if isinstance(item, str) and item.strip())
    return value.strip() if isinstance(value, str) else ""


def recorded_text(text: str) -> str:
    """What a field states for the chart, or "" when it states nothing (the rule above)."""
    stated = text.strip()
    for empty in _EMPTY_CHART:
        if stated.lower().startswith(empty):
            rest = stated[len(empty) :].strip().lstrip(".").strip()
            # Any other mark after it ("(not asked this visit)") states nothing.
            if not (rest.lower().startswith(_STATED_OPEN) and rest.endswith(")")):
                return ""
            stated = rest[len(_STATED_OPEN) : -1].strip().strip('"“”').strip()
            if stated.lower().rstrip(".").strip() in _DENIED_MARKS:
                return DENIES
            break
    return "" if stated.lower().rstrip(".").strip() in _NOTHING_STATED else stated


def stated_history(content: Mapping[str, Any]) -> dict[str, str]:
    """The history fields the note states, by chart key, in the note's order."""
    stated: dict[str, str] = {}
    for section in content.values():
        if not isinstance(section, dict):
            continue
        for field_key, value in section.items():
            key = chart_key_for(field_key)
            text = recorded_text(field_text(value))
            if key is not None and key not in stated and text:
                stated[key] = text
    return stated


def stated_allergies(content: Mapping[str, Any]) -> str:
    """What the note's allergies field states for the chart, "" when nothing."""
    for section in content.values():
        if isinstance(section, dict) and ALLERGIES in section:
            return recorded_text(field_text(section[ALLERGIES]))
    return ""


def recorded_proposals(
    content: Mapping[str, Any],
    recorded_keys: Iterable[str],
    allergy_status: str = "recorded",
) -> list[DraftedProposal]:
    """Each history field the note states and the chart does not record, as written;
    and NKDA when the note states an allergy denial while the chart's
    ``allergy_status`` is ``not_recorded``."""
    recorded = set(recorded_keys)
    proposals = [
        DraftedProposal(
            field_key=key,
            proposed_text=text,
            what_changed=RECORDED_THIS_VISIT,
            evidence=(),
            origin="note",
        )
        for key, text in stated_history(content).items()
        if key not in recorded
    ]
    if allergy_status == "not_recorded" and is_allergy_denial(stated_allergies(content)):
        proposals.append(
            DraftedProposal(
                field_key=ALLERGIES,
                item_key=NKDA,
                proposed_text=NKDA_TEXT,
                what_changed=RECORDED_THIS_VISIT,
                evidence=(),
                origin="note",
            )
        )
    return proposals
