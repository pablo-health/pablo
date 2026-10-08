# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a note records that the chart does not have yet: how the chart is first filled.

One rule for every note. For each history field (the substance-use baseline
included) the chart has no value for, a note field of the same key that
states something is proposed as the field's value, as the note words it,
"Recorded this visit". On an intake that is every history field it covers;
on a follow-up, whose history fields print the chart, it is only a field the
chart was empty for and the visit said something about ("Not recorded"
followed by what was stated this visit). The note is the source, read by
the clinician at review; there is no transcript evidence to cite.

Allergies are not seeded this way: an allergy entry needs its substance,
which free text does not give without guessing. The proposal call proposes
stated allergies with the substance named.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .families import chart_key_for
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


def field_text(value: Any) -> str:
    """A note field's text: a list of text one item a line; a structured field has none."""
    if isinstance(value, list):
        return "\n".join(item.strip() for item in value if isinstance(item, str) and item.strip())
    return value.strip() if isinstance(value, str) else ""


def recorded_text(text: str) -> str:
    """What a field states for the chart, or "" when it states nothing.

    A chart-fed field the chart had nothing for reads "Not recorded", then
    what this visit stated, marked; the statement is what is recorded.
    """
    stated = text.strip()
    for empty in _EMPTY_CHART:
        if stated.lower().startswith(empty):
            rest = stated[len(empty) :].strip().lstrip(".").strip()
            # Anything else after it ("(not asked this visit)") states nothing.
            if not (rest.lower().startswith(_STATED_OPEN) and rest.endswith(")")):
                return ""
            stated = rest[len(_STATED_OPEN) : -1].strip().strip('"“”').strip()
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


def recorded_proposals(
    content: Mapping[str, Any], recorded_keys: Iterable[str]
) -> list[DraftedProposal]:
    """Each history field the note states and the chart does not record, as written."""
    recorded = set(recorded_keys)
    return [
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
