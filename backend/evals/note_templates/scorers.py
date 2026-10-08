# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Deterministic checks on a draft from a starting template.

Each check takes the draft (``{section: {field: text or list}}``, as the
generation service returns it) and the case, and returns its problems: an
empty list is a pass. No model is involved, so a check fails only on what
the draft says.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from app.chart_history.fields import HISTORY_GROUPS, is_history_key
from app.models import Transcript
from app.notes.chart_context import allergies_line
from app.notes.client_present import client_present_end, segments_from_transcript
from app.notes.visit_times import (
    DictatedTime,
    TurnLabel,
    client_present_turns,
    labels_from_stored,
    therapy_seconds,
    turn_spans,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.notes.client_present import TimedSegment

    from evals.note_templates.cases import TemplateCase

Draft = dict[str, dict[str, Any]]

CLIENT_QUOTED = (
    ("risk", "suicidal_homicidal_ideation"),
    ("risk", "self_harm_violence"),
)
"""Fields that must quote what the client said, or read "Not stated."; a
template without one of them is not graded on it."""
OVERALL_RISK = ("risk", "overall_risk")
SELF_HARM = ("risk", "self_harm_violence")
INSTRUCTION = re.compile(r"\b(?:988|911|emergency room|call the office|call me)\b", re.IGNORECASE)
"""What a safety instruction to the client names, which belongs in the
emergency instructions, not in what was reported about self-harm."""
ATTRIBUTION_TAG = re.compile(
    r"\b(?:clinician|therapist|provider)\s+"
    r"(?:dictated|stated|asked|noted|said|reported|documented)\b"
    r"|\b(?:clinician|therapist|provider)\s*:"
    r"|\bclient\s+responded\b"
    r"|\bas noted by (?:the )?(?:clinician|therapist|provider)\b"
    r"|\bdictated addendum\b"
    r"|\bper (?:the )?(?:clinician|therapist|provider)\b",
    re.IGNORECASE,
)
"""An attribution tag: the note is the clinician's own statement, so it never
says who dictated a line of it."""
TRANSCRIPT_CLIENT_TURN = re.compile(r"^\[[\d:]+\] Client: (.*)$", re.MULTILINE)

STATED_MARK = "(stated this visit"
"""What marks a value stated this visit, "(stated this visit)" or
"(stated this visit: ...)", after the chart's value in a chart-fed field."""
ALLERGIES = ("medications", "allergies")
CURRENT_MEDICATIONS = ("medications", "current_medications")
FROM_THE_CHART = "from the chart"
"""How a template's hint opens for a field it fills from the chart."""
PLAN_MEDICATION_FIELDS = (
    ("plan", "medication_plan"),
    ("treatment_plan", "plan_items"),
    ("prescriptions", "prescriptions"),
)
"""Where each template writes the medications started or changed in a visit."""
NO_DIAGNOSES = re.compile(r"^(?:no|none)\b.*\brecorded\b|^none$")
"""What a diagnosis list may say when there is none to name."""

THERAPY_SECTION = "psychotherapy"
HPI_SECTION = "subjective"
NOT_DISCUSSED = "not discussed"
CODE = re.compile(r"\b(?:9\d{4}|G\d{4})\b")
"""Procedure codes: E/M and psychotherapy (9xxxx) and add-on (Gxxxx) codes."""
DIAGNOSIS_CODE = re.compile(r"\b[A-TV-Z]\d{2}(?:\.[0-9A-Z]{1,4})?\b")
CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b")
TIME_FIELDS = frozenset({"encounter.visit_details", "psychotherapy.psychotherapy_time"})
"""Where a note states when the visit or its psychotherapy took place."""
SESSION_TIME = re.compile(
    r"\b(?:start(?:ed|s)?|end(?:ed|s)?|began|(?<!between-)(?<!between )session|"
    r"visit (?:from|time))\b",
    re.IGNORECASE,
)
"""Wording that makes a clock time elsewhere a claim about the visit's own
times. A time the client mentions ("it wears off by 9:00 AM") is not one, nor
is the time of a practice set between sessions ("a worry window at 6:00 PM")."""
MINUTES = re.compile(r"\b(\d+)\s*-?\s*min(?:ute)?s?\b", re.IGNORECASE)
PSYCHOTHERAPY_TIME_LINE = re.compile(r"psychotherapy[^\n.:]*\b(?:time|minutes)\b", re.IGNORECASE)
QUOTED = re.compile('["\u201c\u201d]([^"\u201c\u201d]+)["\u201c\u201d]')
QUOTE_MARK = re.compile('["\u201c\u201d]')
RISK_LEVEL = re.compile(r"\b(?:low|moderate|high|minimal|elevated|imminent)\b", re.IGNORECASE)
TODAY = re.compile(r"\btoday\b", re.IGNORECASE)
STATED_PREFIX = "(stated this visit:"
"""How a chart-fed field marks what the visit stated after the chart's own text."""

_MONTHS = [m.lower() for m in calendar.month_name[1:]]
_DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "%Y-%m-%d"),
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"), "%m/%d/%Y"),
    (re.compile(rf"\b(?:{'|'.join(_MONTHS)})\.? \d{{1,2}},? \d{{4}}\b", re.IGNORECASE), "mdy"),
    (re.compile(rf"\b\d{{1,2}} (?:{'|'.join(_MONTHS)}),? \d{{4}}\b", re.IGNORECASE), "dmy"),
)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Lower case, curly quotes made straight, punctuation and spacing folded."""
    text = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    return " ".join(re.sub(r"[^a-z0-9':]+", " ", text).split())


def _value(draft: Draft, section: str, key: str) -> Any:
    return (draft.get(section) or {}).get(key)


def _text(draft: Draft, section: str, key: str) -> str:
    value = _value(draft, section, key)
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return str(value or "").strip()


def _all_text(draft: Draft) -> list[tuple[str, str]]:
    return [
        (f"{section}.{key}", _text(draft, section, key))
        for section, fields in draft.items()
        for key in (fields or {})
    ]


def is_blank(text: str) -> bool:
    return not text.strip()


def is_not_stated(text: str) -> bool:
    return normalize(text) == "not stated"


def is_not_asked(text: str) -> bool:
    return normalize(text).startswith("not asked")


def calendar_dates(text: str) -> list[date]:
    """Every calendar date written in ``text``, in the forms a note uses."""
    found: list[date] = []
    for pattern, form in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(0).replace(".", "").replace(",", "")
            try:
                if form == "mdy":
                    found.append(datetime.strptime(raw, "%B %d %Y").date())
                elif form == "dmy":
                    found.append(datetime.strptime(raw, "%d %B %Y").date())
                else:
                    found.append(datetime.strptime(raw, form).date())
            except ValueError:
                found.append(date.min)
    return found


def _quote_in_transcript(quote: str, transcript: str) -> bool:
    """Every part of the quote (split at an ellipsis) appears in the transcript."""
    said = normalize(transcript)
    parts = [normalize(p) for p in re.split(r"\.\.\.|…", quote)]
    return all(p in said for p in parts if p)


def client_words(transcript: str) -> str:
    """Everything the client said, one turn per line: what a quotation of the
    client may be drawn from."""
    return "\n".join(TRANSCRIPT_CLIENT_TURN.findall(transcript))


def clinician_words(transcript: str) -> str:
    """Everything the clinician said, the dictated addendum included."""
    return "\n".join(
        line for line in transcript.splitlines() if not TRANSCRIPT_CLIENT_TURN.match(line)
    )


def is_marked(text: str) -> bool:
    return STATED_MARK in text.lower()


def _item_text(item: Any) -> str:
    """A list item as text: a diagnosis comes back as {label, code, status}."""
    if isinstance(item, dict):
        return " ".join(str(item[k]) for k in ("code", "label", "status") if item.get(k))
    return str(item)


def _items(draft: Draft, section: str, key: str) -> list[str]:
    """A list field's items, or a text field as one item; blanks left out."""
    value = _value(draft, section, key)
    items = [_item_text(i) for i in value] if isinstance(value, list) else [str(value or "")]
    return [i for i in items if i.strip()]


def _spec_fields(case: TemplateCase) -> list[tuple[str, str, str]]:
    """The template's fields as (section, key, hint)."""
    return [(s.key, f.key, f.ai_hint) for s in case.spec.sections for f in s.fields]


def _section_of(case: TemplateCase, key: str) -> str | None:
    return next((s for s, k, _ in _spec_fields(case) if k == key), None)


def chart_fed_fields(case: TemplateCase) -> list[tuple[str, str]]:
    """The fields the template fills from the chart: its hint says so, and the
    allergies, which carry the chart's value in every prescriber template."""
    fields = [
        (s, k) for s, k, hint in _spec_fields(case) if hint.lower().startswith(FROM_THE_CHART)
    ]
    if ALLERGIES not in fields and ALLERGIES in {(s, k) for s, k, _ in _spec_fields(case)}:
        fields.append(ALLERGIES)
    return fields


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def codes_only_dictated(draft: Draft, case: TemplateCase) -> list[str]:
    """Billing codes, clock times and minutes appear only as dictated.

    The psychotherapy time lives in one place, the Psychotherapy section: the
    visit details never restate it, so a confirmed window can't leave a
    second, stale copy behind.
    """
    e = case.expected
    problems: list[str] = []
    for path, text in _all_text(draft):
        problems += [
            f"{path}: code {c} was not dictated" for c in CODE.findall(text) if c not in e.codes
        ]
        if path not in TIME_FIELDS and not SESSION_TIME.search(text):
            continue
        problems += [
            f"{path}: time {t} was not dictated" for t in CLOCK.findall(text) if t not in e.times
        ]
    timing = _text(draft, "psychotherapy", "psychotherapy_time")
    problems += [
        f"psychotherapy.psychotherapy_time: {m} minutes was not dictated"
        for m in MINUTES.findall(timing)
        if m not in e.minutes
    ]
    details = _text(draft, "encounter", "visit_details")
    if PSYCHOTHERAPY_TIME_LINE.search(details) or MINUTES.search(details):
        problems.append("encounter.visit_details: restates the psychotherapy time")
    problems += [
        f"encounter.visit_details: dictated code {c} missing" for c in e.codes if c not in details
    ]
    if e.therapy:
        problems += [
            f"psychotherapy.psychotherapy_time: dictated {wanted} missing"
            for wanted in (*e.times, *e.minutes)
            if not re.search(rf"\b{re.escape(wanted)}\b", timing)
        ]
    return problems


def psychotherapy_section(draft: Draft, case: TemplateCase) -> list[str]:
    """Empty when no therapy took place; written when it did."""
    fields = draft.get(THERAPY_SECTION) or {}
    if not case.expected.therapy:
        return [
            f"{THERAPY_SECTION}.{k}: written for a visit with no therapy"
            for k in fields
            if not is_blank(_text(draft, THERAPY_SECTION, k))
        ]
    return [
        f"{THERAPY_SECTION}.{k}: empty for a visit with therapy"
        for k in ("issues_addressed", "modality_interventions")
        if is_blank(_text(draft, THERAPY_SECTION, k))
    ]


def risk_as_said(draft: Draft, case: TemplateCase) -> list[str]:
    """The client's words about harm are quoted, never paraphrased; the overall
    risk is the level the clinician stated, as the clinician's finding; the
    draft never judges a level of its own."""
    fields = {(s, k) for s, k, _ in _spec_fields(case)}
    problems: list[str] = []
    for section, key in CLIENT_QUOTED:
        if (section, key) in fields:
            problems += _client_quoted(draft, case, section, key)
    if SELF_HARM in fields:
        problems += [
            f"risk.self_harm_violence: {w!r} is an instruction to the client, not a finding"
            for w in INSTRUCTION.findall(_text(draft, *SELF_HARM))
        ]
    if OVERALL_RISK in fields:
        problems += _overall_risk(draft, case)
    return problems


def _client_quoted(draft: Draft, case: TemplateCase, section: str, key: str) -> list[str]:
    text = _text(draft, section, key)
    path = f"{section}.{key}"
    wanted = case.expected.risk_quotes.get(key, ())
    if is_not_stated(text):
        return [f"{path}: the client said it, but the field is not stated"] if wanted else []
    quotes = QUOTED.findall(text)
    if not quotes:
        return [f'{path}: the client\'s words are not quoted, nor is it "Not stated."']
    problems = []
    if wanted and not any(w in normalize(q) for q in quotes for w in wanted):
        problems.append(f"{path}: no quotation carries {' or '.join(map(repr, wanted))}")
    problems += [
        f"{path}: quoted {q!r} is not the client's words"
        for q in quotes
        if not _quote_in_transcript(q, client_words(case.transcript))
    ]
    outside = QUOTED.sub(" ", text)
    problems += [f"{path}: {w!r} judged outside a quotation" for w in RISK_LEVEL.findall(outside)]
    return problems


def _overall_risk(draft: Draft, case: TemplateCase) -> list[str]:
    text = _text(draft, *OVERALL_RISK)
    path = "risk.overall_risk"
    stated = case.expected.risk_level
    if stated is None:
        return [] if is_not_stated(text) else [f"{path}: no level was stated: {text!r}"]
    problems = []
    if QUOTED.search(text):
        problems.append(f"{path}: the clinician's finding is quoted")
    levels = {w.lower() for w in RISK_LEVEL.findall(text)}
    if stated not in levels:
        problems.append(f"{path}: the stated level {stated!r} is missing")
    problems += [f"{path}: {w!r} was not stated" for w in sorted(levels - {stated})]
    return problems


def no_attribution_tags(draft: Draft, _case: TemplateCase) -> list[str]:
    """No field says who dictated, asked or answered: the note is the
    clinician's own statement, and a quotation is framed as the client's."""
    return [
        f"{path}: attribution tag {m.group(0)!r}"
        for path, text in _all_text(draft)
        for m in ATTRIBUTION_TAG.finditer(text)
    ]


def findings_unquoted(draft: Draft, case: TemplateCase) -> list[str]:
    """What the clinician dictated is written as findings, with no quotation
    marks; a field that quotes the client does so once."""
    e = case.expected
    problems = [
        f"{path}: the clinician's finding is quoted: {text!r}"
        for path in e.findings
        if QUOTE_MARK.search(text := _text(draft, *path.split(".", 1)))
    ]
    for path, words in e.quoted_once.items():
        quotes = QUOTED.findall(_text(draft, *path.split(".", 1)))
        if len(quotes) != 1:
            problems.append(f"{path}: {len(quotes)} quotations, not one: {quotes!r}")
        elif not any(w in normalize(quotes[0]) for w in words):
            problems.append(f"{path}: the quotation carries none of {words!r}")
    return problems


def safety_plan_only_with_ideation(draft: Draft, case: TemplateCase) -> list[str]:
    """Written when ideation was reported (the clinician described one); never otherwise."""
    text = _text(draft, "risk", "safety_plan")
    written = not (is_blank(text) or is_not_stated(text))
    if case.expected.ideation:
        return [] if written else ["risk.safety_plan: ideation was reported, but no plan written"]
    return ["risk.safety_plan: written though no ideation was reported"] if written else []


def pdmp_line(draft: Draft, case: TemplateCase) -> list[str]:
    """A dictated check carries the calendar date and the finding; else no claim."""
    section = _section_of(case, "pdmp") or "plan"
    path = f"{section}.pdmp"
    text = _text(draft, section, "pdmp")
    findings = case.expected.pdmp_findings
    if findings is None:
        if is_blank(text) or is_not_stated(text):
            return []
        return [f"{path}: claims a check the clinician did not dictate: {text!r}"]
    problems: list[str] = []
    if TODAY.search(text):
        problems.append(f'{path}: says "today" instead of a date')
    dates = calendar_dates(text)
    if not dates:
        problems.append(f"{path}: no calendar date")
    problems += [
        f"{path}: date {d} is not the date of service {case.session_date}"
        for d in dates
        if d != case.session_date
    ]
    said = normalize(text)
    problems += [f"{path}: finding {f!r} missing" for f in findings if f not in said]
    return problems


def telehealth_attestation(draft: Draft, case: TemplateCase) -> list[str]:
    """Telehealth names both entered locations; an office visit never says telehealth."""
    text = normalize(_text(draft, "encounter", "place_of_service"))
    locations = case.expected.telehealth
    if locations is None:
        problems = [] if "in office" in text else ["encounter.place_of_service: not in-office"]
        if "telehealth" in text:
            problems.append("encounter.place_of_service: telehealth for an office visit")
        return problems
    problems = [] if "telehealth" in text else ["encounter.place_of_service: no telehealth"]
    problems += [
        f"encounter.place_of_service: location {loc!r} missing"
        for loc in locations
        if normalize(loc) not in text
    ]
    if "located at" in text:
        problems.append('encounter.place_of_service: "located at" a location; write "in" it')
    if case.expected.client_at_home and "at home" not in text:
        problems.append("encounter.place_of_service: the client said they were at home")
    return problems


ASKED_NO_CHANGE = "(asked this visit: no change)"
NOT_ASKED = "(not asked this visit)"
_SCREEN_MARKS = (ASKED_NO_CHANGE, STATED_PREFIX, NOT_ASKED)


def split_screen(text: str) -> tuple[str, str]:
    """A substance field's baseline text, and the screen suffix that follows it."""
    at = min((i for m in _SCREEN_MARKS if (i := text.find(m)) >= 0), default=len(text))
    return text[:at].rstrip(), text[at:].strip()


def _prints_baseline(case: TemplateCase) -> bool:
    """Whether the template's substance fields print the chart's baseline."""
    return any(
        s == "substance_use" and "substance use baseline" in hint.lower()
        for s, _, hint in _spec_fields(case)
    )


def is_denial(text: str) -> bool:
    return any(w in normalize(text).split() for w in ("denied", "denies"))


def _baseline_then_screen(draft: Draft, case: TemplateCase) -> list[str]:
    e = case.expected
    baseline = {f.key: f.text for f in case.history}
    problems = []
    graded = (*e.substances_asked, *e.substances_not_asked, *e.substances_denied)
    for k in (*graded, *e.substances_stated):
        path = f"substance_use.{k}"
        chart_text, screen = split_screen(_text(draft, "substance_use", k))
        if normalize(chart_text) != normalize(baseline.get(k, "Not recorded")):
            problems.append(f"{path}: not the chart's baseline as recorded")
        answer = (
            screen.removeprefix(STATED_PREFIX).strip(' )"')
            if screen.startswith(STATED_PREFIX)
            else ""
        )
        if k in e.substances_not_asked and screen != NOT_ASKED:
            problems.append(f'{path}: should end "{NOT_ASKED}"')
        if k in e.substances_asked and not (screen == ASKED_NO_CHANGE or answer):
            problems.append(f"{path}: asked, but no screen recorded")
        if k in e.substances_denied and not is_denial(answer):
            problems.append(f"{path}: denied, but not marked as a denial: {screen!r}")
        if k in e.substances_stated and (not answer or is_denial(answer)):
            problems.append(f"{path}: what was said is not marked as stated: {screen!r}")
    return problems


def substances(draft: Draft, case: TemplateCase) -> list[str]:
    """Each substance field is graded the way its template writes it.

    Where the template prints the chart's baseline (the follow-up), a field is
    that baseline, then this visit's screen: "(not asked this visit)" when it
    never came up, "(asked this visit: no change)" or "(stated this visit:
    ...)" when it did, a denial marked as one and anything else said marked as
    stated. Where the template drafts substance use from the visit
    (the evaluation), not asked reads "Not asked", asked records the answer,
    and the chart's baseline is never copied in as this visit's answer.
    """
    if _prints_baseline(case):
        return _baseline_then_screen(draft, case)
    e = case.expected
    problems = [
        f'substance_use.{k}: should read "Not asked"'
        for k in e.substances_not_asked
        if not is_not_asked(_text(draft, "substance_use", k))
    ]
    for k in (*e.substances_asked, *e.substances_denied, *e.substances_stated):
        text = _text(draft, "substance_use", k)
        if is_blank(text) or is_not_asked(text) or is_not_stated(text):
            problems.append(f"substance_use.{k}: asked, but no answer recorded")
        elif k in e.substances_denied and not is_denial(text):
            problems.append(f"substance_use.{k}: denied, but not recorded as a denial")
    problems += [
        f"substance_use.{f.key}: the chart's baseline copied in as this visit's screen"
        for f in case.history
        if normalize(_text(draft, "substance_use", f.key)) == normalize(f.text)
    ]
    return problems


def diagnoses_only_stated(draft: Draft, case: TemplateCase) -> list[str]:
    """Each diagnosis is one the clinician entered or named, a coded one with its
    code; no invented codes. With none entered or named, the list names none."""
    allowed = case.expected.diagnoses
    codes = {d.code for d in allowed if d.code}
    problems: list[str] = []
    items = _items(draft, "assessment", "diagnoses")
    if allowed and not items:
        problems.append("assessment.diagnoses: empty")
    for item in items:
        said = normalize(item)
        named = any(
            (d.code and normalize(d.code) in said) or any(t in said for t in d.terms)
            for d in allowed
        )
        if not named and not (not allowed and NO_DIAGNOSES.search(said)):
            problems.append(f"assessment.diagnoses: {item!r} was not entered or named")
    listed = normalize(" ".join(items))
    problems += [
        f"assessment.diagnoses: {d.code} not named with its code"
        for d in allowed
        if d.code and normalize(d.code) not in listed
    ]
    for path, text in _all_text(draft):
        problems += [
            f"{path}: diagnosis code {c} was not entered"
            for c in DIAGNOSIS_CODE.findall(text)
            if c not in codes
        ]
    return problems


def measures_undated(draft: Draft, case: TemplateCase) -> list[str]:
    """A measure keeps the words used for when it was taken, never a converted date."""
    return [
        f"measures.{key}: calendar date {d} not said"
        for section, key, _ in _spec_fields(case)
        if section == "measures"
        for d in calendar_dates(_text(draft, section, key))
    ]


MEDICATION_HEADINGS = frozenset({"psychiatric:", "other:", "not categorized:"})


def _is_heading(item: str) -> bool:
    return item.strip().lower() in MEDICATION_HEADINGS


def _without_heading(item: str) -> str:
    """A chart line written under its category's heading on one line
    ("Psychiatric: Sertraline 50 mg") is still the chart's line."""
    text = item.strip()
    heading = next((h for h in MEDICATION_HEADINGS if text.lower().startswith(h)), "")
    return text[len(heading) :].strip() or item


def medications_from_chart(draft: Draft, case: TemplateCase) -> list[str]:
    """The current list is the chart's, word for word, and holds nothing changed today;
    what was started or changed is in the plan.

    A medication the client reports taking is allowed after it, marked as stated.
    """
    expected = case.expected.current_medications
    if expected is None:
        return []
    path = "medications.current_medications"
    items = [_without_heading(i) for i in _items(draft, *CURRENT_MEDICATIONS) if not _is_heading(i)]
    chart_lines = expected or ("None recorded",)
    listed = {normalize(i) for i in items}
    wanted = {normalize(line) for line in chart_lines}
    problems = [
        f"{path}: {line!r} is on the chart but not listed as written"
        for line in chart_lines
        if normalize(line) not in listed
    ]
    problems += [
        f"{path}: {item!r} is not on the chart"
        for item in items
        if normalize(item) not in wanted and not is_marked(item)
    ]
    problems += [
        f"{path}: {word!r} was changed this visit; it belongs to the plan"
        for word in case.expected.not_current
        if any(word in normalize(i).split() for i in items)
    ]
    plan = normalize(" ".join(_text(draft, s, k) for s, k in PLAN_MEDICATION_FIELDS))
    problems += [
        f"plan: {word!r} was started or changed this visit, but the plan does not say so"
        for word in case.expected.in_plan
        if word not in plan.split()
    ]
    return problems


def intake_states_meds(draft: Draft, case: TemplateCase) -> list[str]:
    """Each medication the client said they take, and the chart lacks, is in the
    current list and marked as stated this visit: never left out, never unmarked."""
    items = _items(draft, *CURRENT_MEDICATIONS)
    problems: list[str] = []
    for name in case.expected.stated_medications:
        naming = [i for i in items if name in normalize(i)]
        if not naming:
            problems.append(f"medications.current_medications: {name!r} was stated but not listed")
        problems += [
            f"medications.current_medications: {i!r} is not on the chart and not marked as stated"
            for i in naming
            if not is_marked(i)
        ]
    return problems


def allergies_never_dropped(draft: Draft, case: TemplateCase) -> list[str]:
    """The allergies field carries the chart's value, whatever was said, and then
    what was said this visit."""
    chart = case.chart
    path = "medications.allergies"
    said = normalize(_text(draft, *ALLERGIES))
    if chart.allergy_status == "nkda":
        kept = "nkda" in said.split() or "no known drug allergies" in said
    elif chart.allergy_status == "recorded" and chart.allergies:
        kept = all(normalize(a["substance"]) in said for a in chart.allergies)
    else:
        kept = "not recorded" in said
    problems = [] if kept else [f"{path}: the chart's {allergies_line(chart)!r} was dropped"]
    stated = case.expected.allergies_stated
    if stated and not any(words in said for words in stated):
        problems.append(f"{path}: what was said ({' or '.join(map(repr, stated))}) is missing")
    return problems


def suffix_only_where_stated(draft: Draft, case: TemplateCase) -> list[str]:
    """A chart-fed field carries the "(stated this visit" mark when, and only when,
    the visit said something new about it."""
    e = case.expected
    problems = []
    for section, key in chart_fed_fields(case):
        path = f"{section}.{key}"
        marked = is_marked(_text(draft, section, key))
        if path in e.stated_this_visit and not marked:
            problems.append(f"{path}: the visit stated a change, but nothing is marked")
        elif marked and path not in e.stated_this_visit and path not in e.may_state:
            problems.append(f"{path}: marked as stated this visit, but nothing new was stated")
    return problems


def history_from_chart(draft: Draft, case: TemplateCase) -> list[str]:
    """Each history field the template fills from the chart starts with the chart's
    text, word for word, or "Not recorded"; anything after it is marked as stated."""
    recorded = {f.key: f.text for f in case.history}
    problems = []
    for section, key in chart_fed_fields(case):
        if not is_history_key(key):
            continue
        text = normalize(_text(draft, section, key))
        path = f"{section}.{key}"
        chart = normalize(recorded.get(key, "Not recorded"))
        rest = text.removeprefix(chart).strip() if text.startswith(chart) else None
        if rest is None or (rest and not rest.startswith("stated this visit")):
            problems.append(
                f"{path}: not the chart's text as recorded"
                if key in recorded
                else f'{path}: nothing on the chart, so it should read "Not recorded"'
            )
        elif rest.rstrip(":") == "stated this visit":
            problems.append(f"{path}: an empty (stated this visit: ...) suffix")
    return problems


def history_from_visit(draft: Draft, case: TemplateCase) -> list[str]:
    """For a template that takes history from the visit: each field the visit
    covered says what the client said, never "Not recorded" or "Not stated."."""
    covered = case.expected.history_from_visit or {}
    problems = []
    for key, words in covered.items():
        section = next((g.key for g in HISTORY_GROUPS if key in {f.key for f in g.fields}), "")
        text = normalize(_text(draft, section, key))
        path = f"{section}.{key}"
        if text in {"", "not recorded", "not stated", "not asked"}:
            problems.append(f"{path}: covered in the visit, but not written")
        elif not any(w in text for w in words):
            problems.append(f"{path}: carries none of {words!r}")
    return problems


def hpi_by_domain(draft: Draft, case: TemplateCase) -> list[str]:
    """Each symptom domain the visit covered says what was said about it; one
    that never came up reads "Not discussed."."""
    e = case.expected
    problems = []
    for key, words in (e.hpi or {}).items():
        text = normalize(_text(draft, HPI_SECTION, key))
        path = f"{HPI_SECTION}.{key}"
        if text in {"", NOT_DISCUSSED}:
            problems.append(f"{path}: discussed in the visit, but not written")
        elif not any(w in text for w in words):
            problems.append(f"{path}: carries none of {words!r}")
    problems += [
        f'{HPI_SECTION}.{key}: never came up, so it should read "Not discussed."'
        for key in e.hpi_not_discussed
        if normalize(_text(draft, HPI_SECTION, key)) != NOT_DISCUSSED
    ]
    return problems


def counseling_only_as_stated(draft: Draft, case: TemplateCase) -> list[str]:
    """The plan's education and lifestyle counseling hold what the clinician
    said, and are empty when the clinician said nothing of the kind."""
    e = case.expected
    problems = []
    for key, words in (("education_provided", e.education), ("lifestyle_counseling", e.lifestyle)):
        if words is None:
            continue
        items = _items(draft, "plan", key)
        if not words and items:
            problems.append(f"plan.{key}: written, but the clinician gave none: {items!r}")
        elif words and not any(w in normalize(" ".join(items)) for w in words):
            problems.append(f"plan.{key}: carries none of {words!r}")
    return problems


CHECKS: dict[str, Callable[[Draft, TemplateCase], list[str]]] = {
    "codes_only_dictated": codes_only_dictated,
    "psychotherapy_section": psychotherapy_section,
    "risk_as_said": risk_as_said,
    "no_attribution_tags": no_attribution_tags,
    "findings_unquoted": findings_unquoted,
    "safety_plan": safety_plan_only_with_ideation,
    "pdmp_line": pdmp_line,
    "telehealth_attestation": telehealth_attestation,
    "substances": substances,
    "diagnoses_only_stated": diagnoses_only_stated,
    "measures_undated": measures_undated,
    "medications_from_chart": medications_from_chart,
    "intake_states_meds": intake_states_meds,
    "allergies_never_dropped": allergies_never_dropped,
    "suffix_only_where_stated": suffix_only_where_stated,
    "history_from_chart": history_from_chart,
    "history_from_visit": history_from_visit,
    "hpi_by_domain": hpi_by_domain,
    "counseling_only_as_stated": counseling_only_as_stated,
}


def grade(draft: Draft, case: TemplateCase) -> dict[str, list[str]]:
    """Every check's problems, by check name; all empty means the draft passes."""
    return {name: check(draft, case) for name, check in CHECKS.items()}


def recorded_boundary(case: TemplateCase) -> float | None:
    """Where the client left a case drafted as a recorded visit; ``None`` otherwise."""
    if not case.recorded:
        return None
    segments = segments_from_transcript(Transcript(format="txt", content=case.transcript))
    return client_present_end(segments, client_channel_expected=False)


def _seconds(stamp: str) -> float:
    hours, minutes, seconds = (int(part) for part in stamp.split(":"))
    return float(hours * 3600 + minutes * 60 + seconds)


def _present_turns(case: TemplateCase, end: float) -> list[TimedSegment]:
    segments = segments_from_transcript(Transcript(format="txt", content=case.transcript))
    return client_present_turns(segments, end)


def truth_labels(case: TemplateCase, turns: list[TimedSegment]) -> dict[float, TurnLabel]:
    """The case's runs as a label for every client-present turn."""
    runs = sorted((_seconds(stamp), label) for stamp, label in case.segment_labels or ())
    labels: dict[float, TurnLabel] = {}
    for turn in turns:
        held = [label for start, label in runs if start <= turn.start]
        if not held:
            raise ValueError(f"{case.name}: the turn at {turn.start:.0f}s has no label")
        labels[turn.start] = held[-1]
    return labels


def therapy_minutes_table(case: TemplateCase, proposal: dict[str, Any] | None) -> dict[str, Any]:
    """Proposed and labeled therapy, in seconds, and how far apart one turn allows."""
    end = recorded_boundary(case)
    if end is None or case.segment_labels is None:
        return {}
    turns = _present_turns(case, end)
    proposed = labels_from_stored((proposal or {}).get("labels"))
    return {
        "end": end,
        "proposed": therapy_seconds(proposed, turns, end),
        "labeled": therapy_seconds(truth_labels(case, turns), turns, end),
        "one_turn": max((stop - start for start, stop in turn_spans(turns, end)), default=0.0),
        "tail_labeled": sorted(s for s in proposed if s >= end),
        "unlabeled": sum(1 for t in turns if t.start not in proposed),
    }


def therapy_minutes(case: TemplateCase, proposal: dict[str, Any] | None) -> list[str]:
    """The proposed turn labels add up to the labeled therapy, within one turn.

    Never a turn after the client left, never more than the client was
    present, and nothing for a visit with no therapy. Where the clinician
    dictated minutes, the draft returns them as dictated.
    """
    problems: list[str] = []
    wanted = case.expected.minutes
    if wanted:
        stated = DictatedTime.from_reply((proposal or {}).get("dictated"))
        got = stated.minutes if stated else None
        if str(got) not in wanted:
            problems.append(f"dictated minutes read as {got}, not {' or '.join(wanted)}")
    table = therapy_minutes_table(case, proposal)
    if not table:
        return problems
    proposed, labeled = table["proposed"], table["labeled"]
    if table["tail_labeled"]:
        problems.append(f"turns after the client left are labeled: {table['tail_labeled']}")
    if proposed > table["end"]:
        problems.append(f"{proposed:.0f}s of therapy is more than the client was present")
    if labeled == 0 and proposed > 0:
        problems.append(f"{proposed:.0f}s of therapy proposed for a visit with none")
    elif abs(proposed - labeled) > table["one_turn"]:
        problems.append(
            f"{proposed / 60:.1f} therapy minutes proposed, {labeled / 60:.1f} labeled: "
            f"more than one turn ({table['one_turn']:.0f}s) apart"
        )
    return problems
