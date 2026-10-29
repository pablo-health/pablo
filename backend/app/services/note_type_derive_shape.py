# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Deterministic corrections to the shape of a derived note type.

The model proposes; these rules hold whatever it proposed:

- **Only the note is a note.** Sample notes often come with material
  around them: an explanation of how the codes were chosen, a teaching
  aside, a line saying a block is "not part of the note". That text is
  removed before the model sees the sample or the coverage check reads
  it, and a section whose purpose is justifying codes is dropped.
- **Visit facts get a home.** Date of service, visit times, codes, place of
  service, where the client and the clinician were, and telehealth consent
  sit in a note's header, outside its prose sections, and are easy for a
  proposal to leave out. When the samples carry them, the proposal gets an
  Encounter section with a field for each (and an attestation field when
  the visit was telehealth), and the facts known before a visit — place of
  service and both locations — become inputs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .note_type_derive_checks import words

# Phrases a sample uses to say a block is not note content.
_NON_NOTE_MARKERS = re.compile(
    r"not (?:required|needed|necessary|included|part of|to be included)"
    r"(?: in)? (?:your|the|this) (?:note|documentation|record)"
    r"|for (?:educational|teaching|training|illustrat\w*|reference) purposes"
    r"|\brationale\b"
    r"|how (?:the|these|we|i) (?:cpt |billing |e/?m )?codes? "
    r"(?:were|was|are|is) (?:selected|chosen|determined)"
    r"|code selection"
    r"|why (?:this|these|the) codes?",
    re.IGNORECASE,
)

# A section whose job is justifying codes rather than recording the visit.
_RATIONALE_SECTION = re.compile(
    r"\b(?:rationale|justification|justify|justifying)\b"
    r"|\bcod(?:e|ing) selection\b"
    r"|\bbilling (?:support|explanation|reasoning)\b",
    re.IGNORECASE,
)

_HEADING_MAX_WORDS = 8


_LIST_MARK = re.compile(r"^(?:\d+[.)]|[\u2022\u25cb\u25cf\u25aa*-])\s")


def _plain_heading(line: str) -> bool:
    """A heading that opens a part of the note: short, unnumbered, holding no value.

    "Plan" and "Follow-up:" are plain headings; "1. Problems addressed: Low"
    and "Risk: Moderate" are items, which do not end a block.
    """
    text = line.strip()
    if not text or len(words(text)) > _HEADING_MAX_WORDS or _LIST_MARK.match(text):
        return False
    if ":" in text.rstrip(":"):
        return False
    return text.endswith(":") or not text.endswith((".", "!", "?", ","))


@dataclass(frozen=True)
class StrippedSample:
    text: str
    removed_lines: int


def strip_non_note(sample: str) -> StrippedSample:
    """The sample without blocks it marks as instructional or as code rationale.

    A marker — in a heading or in body text — starts a block that runs to the
    next plain heading (or the end of the note, where such blocks usually
    sit). Numbered items and "Label: value" lines inside it stay in it.
    """
    lines = sample.splitlines()
    keep = [True] * len(lines)
    in_block = False
    for n, line in enumerate(lines):
        if not line.strip():
            continue
        marked = bool(_NON_NOTE_MARKERS.search(line))
        if _plain_heading(line):
            in_block = marked
        elif marked:
            in_block = True
        if in_block:
            keep[n] = False
    kept = [line for line, k in zip(lines, keep, strict=True) if k]
    return StrippedSample("\n".join(kept), removed_lines=keep.count(False))


def drop_rationale_sections(body: dict[str, Any]) -> list[str]:
    """Remove sections that justify codes; return the labels removed."""
    removed = [
        s["label"]
        for s in body["sections"]
        if _RATIONALE_SECTION.search(f"{s['label']} {s['key'].replace('_', ' ')}")
    ]
    body["sections"] = [s for s in body["sections"] if s["label"] not in removed]
    return removed


# ---------------------------------------------------------------------------
# Visit facts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeaderFact:
    key: str
    label: str
    hint: str
    detect: re.Pattern[str]
    # A field already holds the fact when its label has all of one group's words.
    covered_by: tuple[frozenset[str], ...]
    before_visit: bool = False


HEADER_FACTS: tuple[HeaderFact, ...] = (
    HeaderFact(
        "date_of_service",
        "Date of service",
        "The date the visit took place.",
        re.compile(r"\bdate of service\b|\bDOS\b|\bsession date\b|\bvisit date\b", re.I),
        (frozenset({"date"}),),
    ),
    HeaderFact(
        "visit_times",
        "Visit times",
        "Start and end times, and minutes, exactly as the clinician stated them.",
        re.compile(
            r"\b(?:start|end|begin|stop)\s*time\b|\btime (?:in|out)\b"
            r"|\b\d{1,2}:\d{2}\s*(?:am|pm)?\s*(?:-|\u2013|to)\s*\d{1,2}:\d{2}",
            re.I,
        ),
        (frozenset({"time"}), frozenset({"times"}), frozenset({"minutes"})),
    ),
    HeaderFact(
        "visit_codes",
        "Visit codes",
        "Each code exactly as the clinician stated it; never inferred.",
        re.compile(r"\bcpt\b|\be/?m code\b|\b99[2-4]\d{2}\b|\b908\d{2}\b|\b9079\d\b", re.I),
        (frozenset({"code"}), frozenset({"codes"}), frozenset({"cpt"})),
    ),
    HeaderFact(
        "place_of_service",
        "Place of service",
        "Where the visit took place, as entered for the visit.",
        re.compile(r"\bplace of service\b|\bPOS\b", re.I),
        (frozenset({"place", "service"}), frozenset({"pos"})),
        before_visit=True,
    ),
    HeaderFact(
        "client_location",
        "Client location",
        "Where the client was during the visit.",
        re.compile(
            r"\b(?:client|patient)(?:'s)? (?:physical )?location\b"
            r"|\blocation of (?:the )?(?:client|patient)\b",
            re.I,
        ),
        (frozenset({"client", "location"}), frozenset({"patient", "location"})),
        before_visit=True,
    ),
    HeaderFact(
        "provider_location",
        "Provider location",
        "Where the clinician was during the visit.",
        re.compile(
            r"\b(?:provider|clinician|therapist)(?:'s)? (?:physical )?location\b"
            r"|\blocation of (?:the )?(?:provider|clinician)\b",
            re.I,
        ),
        (frozenset({"provider", "location"}), frozenset({"clinician", "location"})),
        before_visit=True,
    ),
)

_TELEHEALTH = re.compile(
    r"\btele(?:health|medicine|psychiatry)\b|\bvideo visit\b|\baudio[- ]only\b", re.I
)
_ATTESTATION = HeaderFact(
    "telehealth_attestation",
    "Telehealth attestation",
    "The client's consent to telehealth and the clinician's attestation, as stated.",
    _TELEHEALTH,
    (frozenset({"attestation"}), frozenset({"attest"}), frozenset({"consent"})),
)

_ENCOUNTER_SECTION = re.compile(r"\b(?:encounter|visit|session|header)\b", re.I)


def header_facts(samples: list[str]) -> list[HeaderFact]:
    """The visit facts the samples carry, in note order; attestation last."""
    text = "\n".join(samples)
    found = [fact for fact in HEADER_FACTS if fact.detect.search(text)]
    if _TELEHEALTH.search(text):
        found.append(_ATTESTATION)
    return found


def _covered(fact: HeaderFact, body: dict[str, Any]) -> bool:
    for section in body["sections"]:
        for fld in section["fields"]:
            terms = set(words(f"{fld['label']} {fld['key'].replace('_', ' ')}"))
            if any(group <= terms for group in fact.covered_by):
                return True
    return False


def ensure_encounter(body: dict[str, Any], samples: list[str]) -> list[str]:
    """Give each visit fact the samples carry a field, and the pre-visit ones an input.

    Missing fields go into the proposal's own encounter section when it has
    one, or a new Encounter section placed first. Returns the keys added.
    """
    facts = header_facts(samples)
    missing = [f for f in facts if not _covered(f, body)]
    added: list[str] = []
    if missing:
        section = next((s for s in body["sections"] if _ENCOUNTER_SECTION.search(s["label"])), None)
        if section is None:
            section = {"key": "encounter", "label": "Encounter", "fields": []}
            body["sections"].insert(0, section)
        taken = {f["key"] for f in section["fields"]}
        for fact in missing:
            if fact.key not in taken:
                section["fields"].append(
                    {"key": fact.key, "label": fact.label, "kind": "text", "ai_hint": fact.hint}
                )
                added.append(fact.key)
    inputs = body.setdefault("inputs", [])
    input_terms = [set(words(f"{i['label']} {i['key'].replace('_', ' ')}")) for i in inputs]
    for fact in facts:
        if not fact.before_visit:
            continue
        if any(any(group <= terms for group in fact.covered_by) for terms in input_terms):
            continue
        inputs.append(
            {"key": fact.key, "label": fact.label, "kind": "text", "options": [], "required": False}
        )
        added.append(f"inputs.{fact.key}")
    return added
