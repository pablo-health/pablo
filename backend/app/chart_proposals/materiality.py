# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether a history proposal is worth the clinician's attention, decided in code.

The proposal call is asked to propose only lasting changes, and mostly does;
what still gets through is a restatement, a passing event, a detail the
clinician asked about rather than one the client told, the present visit
written into a field about the past, or a fact the chart already keeps in
another field. Each costs the clinician a discard at sign, and enough of them
teach a clinician to stop reading the step. So the reply's history proposals
are checked here after their evidence is, and one that fails is not offered.
It is kept on the run with the reason (:class:`Considered`): considered, not
offered.

The medication list and the allergies are not checked here; their families
have their own rules (:mod:`.families`).

Words are compared normalised: lowercase, numbers as digits, dates as ISO
where they parse, stop words out, a plural ``s`` dropped. The word lists are
committed data beside this module (``materiality.json``), each documented
there. A history proposal is offered only when all four hold:

1. **Novelty.** The proposal has a word or a number the chart's text for the
   field does not, and is not that text reworded: at a token-set Jaccard
   similarity of :data:`PARAPHRASE_JACCARD` or more, what it adds must carry
   a state marker, an entity, a name or a number. Where the extraction beside
   the draft covers the field (a type that prints it from the chart), the
   extraction must have found the fact said this visit: it leaves out a field
   the visit only repeated. It files a fact by its best guess at the field,
   so a statement it filed under another field counts when it shares at least
   :data:`ELSEWHERE_MIN_WORDS` of the words the proposal adds
   (:func:`heard_about`).
2. **Lasting state.** The new words include a state-change marker
   (``started``, ``moved``, ``finalized``, ``laid off``, ``no longer``) or a
   new named entity (a person, place, clinician, program, diagnosis or
   treatment the chart does not name, or a capitalised name it lacks). New
   words that include a transient-event word (an appointment, a scheduled
   interview, a weekday, "expecting") with no state marker are not lasting.
   And a line the proposal rests on (the client's, or the clinician's
   dictation) says the change itself: it carries a marker of the same kind as
   one the proposal adds (the markers are grouped by kind, so a client's
   "started" grounds a note's "began"), or the entity or name the proposal
   adds. "Weekly therapy begun" written from "Three sessions." is an
   inference, not something said.
3. **Source.** A transcript proposal rests on what the client said or on what
   the clinician dictated after the client's last line: at least one line it
   cites is one of those. One citing only the clinician's interview lines
   (questions and reflections before the client's last line) is not offered.
   A cited question beside the client's answer is fine; that is how a
   substance screen is evidenced. A transcript with no client line (one
   microphone, or a dictation) cannot tell the two apart and is not checked.
4. **Not already elsewhere.** The fact is not kept in another of the chart's
   places: new words that one other history field already holds (at least
   :data:`ELSEWHERE_MIN_WORDS` of them), a diagnosis the problem list already
   has, a medication the list has or this visit proposes, a dose, or a lab
   result. A history field takes neither of the last two; a substance field
   and ``medication_trials`` record amounts of their own, but a substance
   field still takes no dose of a medication the list carries. And a field
   about the past (``past_self_harm``, ``hospitalizations``,
   ``prior_diagnoses``, ``medication_trials``, ``trauma_history``) is not
   given this visit's present: new words with a present-time marker
   (``currently``, ``this visit``, ``most mornings``) and no past-time
   marker are refused as ``present-not-past``.

An empty field takes a baseline, a denial included: anything stated is new,
and lasting by being the field's first fact. Only the source, the
extraction and the problem list are checked for it.

The thresholds come from the longitudinal episodes' history proposals on the
model the product runs: every one those runs made, the ones their fixtures
expect and the ones they do not.

- :data:`PARAPHRASE_JACCARD` = 0.85. Similarity alone does not separate the
  two. An amendment keeps the chart's text and adds a clause, so expected
  amendments run from 0.11 to 0.90 against the chart, and unwanted ones from
  0.09 to 0.75. Similarity therefore refuses nothing on its own. Above 0.85 a
  proposal is a rewording unless what it adds carries something. The one
  expected proposal above 0.85, a divorce finalized on a date, carries both.
- :data:`ELSEWHERE_MIN_WORDS` = 2. A single word shared with another field
  ("sister") is a coincidence, not the fact.

Where the rules trade a missed proposal against an unwanted one, they prefer
silence: an unoffered change is still in the note, which the clinician signs,
and can be added to the chart by hand; an unwanted proposal is a discard at
every sign.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .models import ConsideredReason

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .models import DraftedProposal

Reason = ConsideredReason
"""Why a proposal was considered and not offered."""

Speaker = Literal["client", "dictated", "interview", "unknown"]
"""Who a cited line is from: the client; the clinician after the client's last line
(their dictation); the clinician before it (the interview); or not known (a
transcript with no client line, or an imported document's paragraph)."""

PARAPHRASE_JACCARD = 0.85
"""A proposal whose token set is at least this similar to the chart's text only rewords it."""

ELSEWHERE_MIN_WORDS = 2
"""How many new words another field must hold, all of them, for the fact to be elsewhere."""

_CURLY_APOSTROPHE = chr(0x2019)
"""How a typed transcript often writes an apostrophe."""

_SHORTEST_PLURAL = 3
"""A word this short or shorter keeps its final s ("gas", "bus")."""

_LISTS_PATH = Path(__file__).with_name("materiality.json")


@dataclass(frozen=True)
class Verdict:
    admitted: bool
    reason: Reason | None = None


ADMITTED = Verdict(admitted=True)


@dataclass(frozen=True)
class CitedLine:
    """A line a proposal cites, as the transcript has it, and who it is from."""

    text: str
    speaker: Speaker


@dataclass(frozen=True)
class OtherFields:
    """What the chart holds outside the proposal's field, for rule 4."""

    history: Mapping[str, str]
    """The other history fields' text, by key."""
    problems: Sequence[str] = ()
    """The problem list's diagnosis labels."""
    medications: Sequence[str] = ()
    """The listed medications' names, and those this visit proposes to the list."""


@dataclass(frozen=True)
class _Lists:
    stop: frozenset[str]
    state: tuple[tuple[str, ...], ...]
    state_groups: Mapping[str, tuple[tuple[str, ...], ...]]
    transient: tuple[tuple[str, ...], ...]
    entities: tuple[tuple[str, ...], ...]
    present: tuple[tuple[str, ...], ...]
    past: tuple[tuple[str, ...], ...]
    past_fields: frozenset[str]
    dose_units: frozenset[str]
    dose_fields: frozenset[str]
    lab_words: frozenset[str]
    diagnosis_filler: frozenset[str]


_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "once": 1, "twice": 2,
    "half": 0.5, "single": 1, "double": 2,
}  # fmt: skip

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        (
            "january", "february", "march", "april", "may", "june", "july",
            "august", "september", "october", "november", "december",
        )
    )
}  # fmt: skip
_MONTH_ABBREVIATIONS = {m[:3]: n for m, n in _MONTHS.items()} | {"sept": 9}

_MONTH = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DAY_MONTH_YEAR = re.compile(_MONTH + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})")
_MONTH_YEAR = re.compile(_MONTH + r"\.?\s+(?:of\s+)?(\d{4})")
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_WORD = re.compile(r"\d+(?:\.\d+)?|[a-z][a-z0-9]*")
_CAPITALISED = re.compile(r"\b[A-Z][A-Za-z0-9]+\b")
_SENTENCE_START = re.compile(r"(?:^|[.;:!?]\s+|\n)\s*[\"'(]*([A-Z][A-Za-z0-9]*)")


def _month(name: str) -> int:
    return _MONTHS.get(name, _MONTH_ABBREVIATIONS.get(name[:4].rstrip("t"), 0)) or (
        _MONTH_ABBREVIATIONS.get(name[:3], 0)
    )


def _iso_dates(text: str) -> str:
    def day_month_year(m: re.Match[str]) -> str:
        return f" {m.group(3)}-{_month(m.group(1)):02d}-{int(m.group(2)):02d} "

    def month_year(m: re.Match[str]) -> str:
        return f" {m.group(2)}-{_month(m.group(1)):02d} "

    def numeric(m: re.Match[str]) -> str:
        return f" {m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d} "

    text = _DAY_MONTH_YEAR.sub(day_month_year, text)
    text = _MONTH_YEAR.sub(month_year, text)
    return _NUMERIC_DATE.sub(numeric, text)


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _stem(word: str) -> str:
    """A plural or possessive ``s`` dropped; nothing else."""
    if len(word) > _SHORTEST_PLURAL and word.endswith("ies"):
        return word[:-3] + "y"
    if (
        len(word) > _SHORTEST_PLURAL
        and word.endswith("s")
        and not word.endswith(("ss", "us", "is"))
    ):
        return word[:-1]
    return word


def words(text: str) -> list[str]:
    """``text`` lowercased, dates ISO, numbers as digits, each word stemmed; stop words kept."""
    lowered = _iso_dates(text.lower().replace(_CURLY_APOSTROPHE, "'").replace("'s ", " "))
    out: list[str] = []
    for raw in re.findall(r"\d{4}-\d{2}(?:-\d{2})?|" + _WORD.pattern, lowered):
        if raw in _NUMBER_WORDS:
            out.append(_number(_NUMBER_WORDS[raw]))
        else:
            out.append(_stem(raw))
    return out


def _phrases(entries: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(words(entry)) for entry in entries if words(entry))


@cache
def lists() -> _Lists:
    """The committed word lists (``materiality.json``), read once."""
    raw = json.loads(_LISTS_PATH.read_text(encoding="utf-8"))
    groups = {name: _phrases(entries) for name, entries in raw["state_markers"]["groups"].items()}
    return _Lists(
        stop=frozenset(w for entry in raw["stop_words"] for w in words(entry)),
        state=tuple(dict.fromkeys(p for g in groups.values() for p in g)),
        state_groups=groups,
        transient=_phrases(raw["transient"]["words"]),
        entities=_phrases(raw["named_entities"]["words"]),
        present=_phrases(raw["present_markers"]["words"]),
        past=_phrases(raw["past_markers"]["words"]),
        past_fields=frozenset(raw["past_fields"]["fields"]),
        dose_units=frozenset(w for e in raw["medication_terms"]["dose_units"] for w in words(e)),
        dose_fields=frozenset(raw["medication_terms"]["dose_fields"]),
        lab_words=frozenset(w for e in raw["medication_terms"]["lab_words"] for w in words(e)),
        diagnosis_filler=frozenset(w for e in raw["diagnosis_filler"]["words"] for w in words(e)),
    )


def content_words(text: str) -> set[str]:
    """The normalised words of ``text`` with the stop words out."""
    stop = lists().stop
    return {w for w in words(text) if w not in stop}


def heard_about(
    field_key: str,
    proposed_text: str,
    chart_field_text: str,
    said: Mapping[str, Sequence[str]],
) -> list[str] | None:
    """What the extraction found said this visit about a proposal's fact: its own
    field's statements, or, when it filed none there, a statement under another field
    that has at least :data:`ELSEWHERE_MIN_WORDS` of the words the proposal adds. The
    extraction files a fact by its best guess at the field (a father's stroke under
    family psychiatric history), and the question here is whether the visit said the
    fact at all, not where. ``None`` when the extraction does not read the visit for
    ``field_key``."""
    if field_key not in said:
        return None
    own = [s for s in said[field_key] if s.strip()]
    if own:
        return own
    added = content_words(proposed_text) - content_words(chart_field_text)
    return [
        s
        for key, statements in said.items()
        if key != field_key
        for s in statements
        if len(added & content_words(s)) >= ELSEWHERE_MIN_WORDS
    ]


def _has_phrase(sequence: Sequence[str], phrase: tuple[str, ...]) -> bool:
    n = len(phrase)
    return any(tuple(sequence[i : i + n]) == phrase for i in range(len(sequence) - n + 1))


def _new_phrases(
    proposal: Sequence[str], chart: Sequence[str], phrases: tuple[tuple[str, ...], ...]
) -> list[tuple[str, ...]]:
    """The phrases ``proposal`` has and ``chart`` does not."""
    return [p for p in phrases if _has_phrase(proposal, p) and not _has_phrase(chart, p)]


def _capitalised(text: str) -> set[str]:
    """Capitalised words that do not open a sentence: names of people, places, employers."""
    openers = {m.group(1) for m in _SENTENCE_START.finditer(text)}
    found = set()
    for match in _CAPITALISED.finditer(text):
        word = match.group(0)
        if word in openers or word.lower() in _MONTHS or word.lower() in _MONTH_ABBREVIATIONS:
            continue
        if word.lower() in {"i", "monday", "tuesday", "wednesday", "thursday", "friday"}:
            continue
        if word.lower() in {"saturday", "sunday"}:
            continue
        found.add(word.lower())
    return found


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def _new_dose(new: set[str], sequence: Sequence[str]) -> bool:
    """Whether ``new`` includes a number given in a dose unit ("0.75 mg")."""
    units = lists().dose_units
    return any(
        re.fullmatch(r"\d+(?:\.\d+)?", w) and sequence[i + 1] in units and w in new
        for i, w in enumerate(sequence[:-1])
    )


def _is_dose_or_lab(
    new: set[str], sequence: Sequence[str], field_key: str, medications: Sequence[str]
) -> bool:
    """A lab result anywhere; a dose in a field that records no amounts of its own; and,
    in one that does (a substance), a dose of a medication the list carries."""
    vocab = lists()
    if new & vocab.lab_words:
        return True
    if not _new_dose(new, sequence):
        return False
    if field_key not in vocab.dose_fields:
        return True
    if field_key == "medication_trials":
        return False
    return any(content_words(m) & set(sequence) for m in medications)


def _diagnosis_words(label: str) -> set[str]:
    return {w for w in content_words(label) if w not in lists().diagnosis_filler}


def _elsewhere(field_key: str, new: set[str], sequence: Sequence[str], others: OtherFields) -> bool:
    if _is_dose_or_lab(new, sequence, field_key, others.medications):
        return True
    named = {w for w in new if not re.fullmatch(r"[\d.\-]+", w)}
    if field_key != "medication_trials":
        for medication in others.medications:
            if content_words(medication) & named:
                return True
    if _listed_diagnosis(named, others.problems):
        return True
    if len(named) >= ELSEWHERE_MIN_WORDS:
        for key, text in others.history.items():
            if key != field_key and text and named <= content_words(text):
                return True
    return False


def _listed_diagnosis(new: set[str], problems: Sequence[str]) -> bool:
    """Whether ``new`` names nothing but a diagnosis the problem list has."""
    named = {w for w in new if not re.fullmatch(r"[\d.\-]+", w)} - lists().diagnosis_filler
    return bool(named) and any(named <= _diagnosis_words(label) for label in problems)


def _present_not_past(field_key: str, proposal: Sequence[str], chart: Sequence[str]) -> bool:
    vocab = lists()
    if field_key not in vocab.past_fields:
        return False
    present = _new_phrases(proposal, chart, vocab.present)
    past = _new_phrases(proposal, chart, vocab.past)
    return bool(present) and not past


def admit(
    proposal: DraftedProposal,
    chart_field_text: str,
    statements_for_field: Sequence[str] | None,
    cited_segments: Sequence[CitedLine],
    other_chart_fields: OtherFields,
) -> Verdict:
    """Whether a history proposal is offered, and if not, why (see the module).

    ``statements_for_field`` is what the extraction beside the draft found said
    this visit about the field; ``None`` when the extraction does not cover the
    field (a field the note type drafts itself, an imported document) or did not
    run. ``cited_segments`` are the lines the proposal cites."""
    compared = _Compared.of(
        proposal.field_key, proposal.proposed_text, chart_field_text, other_chart_fields
    )
    unheard = statements_for_field is not None and not any(s.strip() for s in statements_for_field)
    if cited_segments and all(c.speaker == "interview" for c in cited_segments):
        reason: Reason | None = "interview"
    elif unheard:
        # The extraction covers the field and found nothing said: only repeated.
        reason = "novelty"
    elif not chart_field_text.strip():
        reason = _baseline(compared)
    else:
        reason = _novelty(compared) or _placement(compared) or _lasting(compared, cited_segments)
    return ADMITTED if reason is None else Verdict(admitted=False, reason=reason)


@dataclass(frozen=True)
class _Compared:
    """A proposal's words beside the chart's for its field, and what it adds."""

    field_key: str
    text: str
    proposal: list[str]
    """The proposal's words with the stop words out: what novelty and placement compare."""
    chart: list[str]
    proposal_all: list[str]
    """Every word of the proposal, stop words kept: what a marker phrase is matched in,
    since "no longer" and "moved in" are made partly of stop words."""
    chart_all: list[str]
    new: set[str]
    state: list[tuple[str, ...]]
    entities: list[tuple[str, ...]]
    names: set[str]
    others: OtherFields

    @classmethod
    def of(cls, field_key: str, text: str, chart_text: str, others: OtherFields) -> _Compared:
        vocab = lists()
        proposal_all, chart_all = words(text), words(chart_text)
        proposal = [w for w in proposal_all if w not in vocab.stop]
        chart = [w for w in chart_all if w not in vocab.stop]
        added_names = _capitalised(text) - _capitalised(chart_text)
        return cls(
            field_key=field_key,
            text=text,
            proposal=proposal,
            chart=chart,
            proposal_all=proposal_all,
            chart_all=chart_all,
            new=set(proposal) - set(chart),
            state=_new_phrases(proposal_all, chart_all, vocab.state),
            entities=_new_phrases(proposal_all, chart_all, vocab.entities),
            names={n for n in added_names if _stem(n) not in chart},
            others=others,
        )


def _baseline(c: _Compared) -> Reason | None:
    """An empty field takes a baseline, a denial included: anything stated is new, and
    lasting by being the field's first fact. Unless the problem list already has it."""
    if not c.text.strip():
        return "novelty"
    return "elsewhere" if _listed_diagnosis(set(c.proposal), c.others.problems) else None


def _novelty(c: _Compared) -> Reason | None:
    """Rule 1: something new, and not the chart's text reworded. A text close to the
    chart's is a rewording unless what it adds carries a state change, an entity, a
    name or a number."""
    figures = {w for w in c.new if re.fullmatch(r"[\d.\-]+", w)}
    similar = jaccard(set(c.proposal), set(c.chart)) >= PARAPHRASE_JACCARD
    if not c.new or (similar and not (c.state or c.entities or c.names or figures)):
        return "novelty"
    return None


def _placement(c: _Compared) -> Reason | None:
    """Rule 4: not this visit's present in a field about the past, and not a fact the
    chart keeps in another place."""
    if _present_not_past(c.field_key, c.proposal_all, c.chart_all):
        return "present-not-past"
    return "elsewhere" if _elsewhere(c.field_key, c.new, c.proposal, c.others) else None


def _lasting(c: _Compared, cited: Sequence[CitedLine]) -> Reason | None:
    """Rule 2: a lasting change, not a passing event, and one that was said."""
    transient = _new_phrases(c.proposal_all, c.chart_all, lists().transient)
    passing = bool(transient) and not c.state
    unmarked = not (c.state or c.entities or c.names)
    if passing or unmarked or not _said(cited, c.state, c.entities, c.names):
        return "transient"
    return None


def _said(
    cited: Sequence[CitedLine],
    state: Sequence[tuple[str, ...]],
    entities: Sequence[tuple[str, ...]],
    names: set[str],
) -> bool:
    """Whether the lines a proposal rests on (not the interview) say its lasting change
    themselves: a state marker of the same kind as one it adds (a client's "started"
    for a note's "began"), or an entity or a name it adds. "Weekly therapy begun"
    written from "Three sessions." is the model's inference, not what was said. A
    proposal citing no such line is not checked."""
    told = [c for c in cited if c.speaker != "interview"]
    if not told:
        return True
    vocab = lists()
    sequence = [w for c in told for w in words(c.text)]
    if names & set(sequence) or any(_has_phrase(sequence, p) for p in entities):
        return True
    kinds = [g for g in vocab.state_groups.values() if any(p in g for p in state)]
    return any(_has_phrase(sequence, p) for g in kinds for p in g)
