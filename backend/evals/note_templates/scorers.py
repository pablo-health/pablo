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

from app.chart_history.fields import HISTORY_GROUPS, SUBSTANCE_USE

if TYPE_CHECKING:
    from collections.abc import Callable

    from evals.note_templates.cases import TemplateCase

Draft = dict[str, dict[str, Any]]

RISK_QUOTED = (
    ("risk", "suicidal_homicidal_ideation"),
    ("risk", "self_harm_violence"),
    ("risk", "overall_risk"),
)
"""Fields that must quote what was said, or read "Not stated."."""

THERAPY_SECTION = "psychotherapy"
CODE = re.compile(r"\b(?:9\d{4}|G\d{4})\b")
"""Procedure codes: E/M and psychotherapy (9xxxx) and add-on (Gxxxx) codes."""
DIAGNOSIS_CODE = re.compile(r"\b[A-TV-Z]\d{2}(?:\.[0-9A-Z]{1,4})?\b")
CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b")
MINUTES = re.compile(r"\b(\d+)\s*-?\s*min(?:ute)?s?\b", re.IGNORECASE)
PSYCHOTHERAPY_TIME_LINE = re.compile(r"psychotherapy[^\n.:]*\b(?:time|minutes)\b", re.IGNORECASE)
QUOTED = re.compile('["\u201c\u201d]([^"\u201c\u201d]+)["\u201c\u201d]')
RISK_LEVEL = re.compile(r"\b(?:low|moderate|high|minimal|elevated|imminent)\b", re.IGNORECASE)
TODAY = re.compile(r"\btoday\b", re.IGNORECASE)

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


def risk_quoted(draft: Draft, case: TemplateCase) -> list[str]:
    """Each risk field quotes what was said, or reads "Not stated."; never a judgment."""
    problems: list[str] = []
    for section, key in RISK_QUOTED:
        text = _text(draft, section, key)
        path = f"{section}.{key}"
        if is_not_stated(text):
            continue
        quotes = QUOTED.findall(text)
        if not quotes:
            problems.append(f'{path}: neither a quotation nor "Not stated."')
            continue
        problems += [
            f"{path}: quoted {q!r} was not said"
            for q in quotes
            if not _quote_in_transcript(q, case.transcript)
        ]
        outside = QUOTED.sub(" ", text)
        problems += [
            f"{path}: {w!r} judged outside a quotation" for w in RISK_LEVEL.findall(outside)
        ]
    return problems


def safety_plan_only_with_ideation(draft: Draft, case: TemplateCase) -> list[str]:
    """No ideation, self-harm or violence was reported in any case here."""
    del case
    text = _text(draft, "risk", "safety_plan")
    if is_blank(text) or is_not_stated(text):
        return []
    return ["risk.safety_plan: written though no ideation was reported"]


def pdmp_line(draft: Draft, case: TemplateCase) -> list[str]:
    """A dictated check carries the calendar date and the finding; else no claim."""
    text = _text(draft, "plan", "pdmp")
    findings = case.expected.pdmp_findings
    if findings is None:
        if is_blank(text) or is_not_stated(text):
            return []
        return [f"plan.pdmp: claims a check the clinician did not dictate: {text!r}"]
    problems: list[str] = []
    if TODAY.search(text):
        problems.append('plan.pdmp: says "today" instead of a date')
    dates = calendar_dates(text)
    if not dates:
        problems.append("plan.pdmp: no calendar date")
    problems += [
        f"plan.pdmp: date {d} is not the date of service {case.session_date}"
        for d in dates
        if d != case.session_date
    ]
    said = normalize(text)
    problems += [f"plan.pdmp: finding {f!r} missing" for f in findings if f not in said]
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
    return problems


def substances(draft: Draft, case: TemplateCase) -> list[str]:
    """Not asked reads "Not asked"; asked records the answer."""
    e = case.expected
    problems = [
        f'substance_use.{k}: should read "Not asked"'
        for k in e.substances_not_asked
        if not is_not_asked(_text(draft, "substance_use", k))
    ]
    for k in e.substances_asked:
        text = _text(draft, "substance_use", k)
        if is_blank(text) or is_not_asked(text) or is_not_stated(text):
            problems.append(f"substance_use.{k}: asked, but no answer recorded")
    return problems


def diagnoses_only_stated(draft: Draft, case: TemplateCase) -> list[str]:
    """Each diagnosis is one the clinician entered or named; no invented codes."""
    allowed = case.expected.diagnoses
    codes = {d.code for d in allowed if d.code}
    problems: list[str] = []
    items = _value(draft, "assessment", "diagnoses") or []
    if not items:
        problems.append("assessment.diagnoses: empty")
    for item in items:
        said = normalize(str(item))
        named = any(
            (d.code and normalize(d.code) in said) or any(t in said for t in d.terms)
            for d in allowed
        )
        if not named:
            problems.append(f"assessment.diagnoses: {item!r} was not entered or named")
    for path, text in _all_text(draft):
        problems += [
            f"{path}: diagnosis code {c} was not entered"
            for c in DIAGNOSIS_CODE.findall(text)
            if c not in codes
        ]
    return problems


def measures_undated(draft: Draft, case: TemplateCase) -> list[str]:
    """A measure keeps the words used for when it was taken, never a converted date."""
    del case
    text = _text(draft, "measures", "measures_reviewed")
    return [f"measures.measures_reviewed: calendar date {d} not said" for d in calendar_dates(text)]


MEDICATION_HEADINGS = frozenset({"psychiatric:", "other:", "not categorized:"})


STATED_PREFIX = "(stated this visit:"


def is_stated_this_visit(item: str) -> bool:
    """A list item the visit added after the chart's list: ``(stated this visit: ...)``."""
    item = item.strip()
    return item.startswith(STATED_PREFIX) and item.endswith(")")


def split_stated(text: str) -> tuple[str, str | None]:
    """A chart-fed field's chart text, and what the visit appended after it, if anything."""
    head, sep, tail = text.partition(STATED_PREFIX)
    if not sep:
        return text, None
    return head.rstrip(), tail.rstrip().removesuffix(")")


def medications_from_chart(draft: Draft, case: TemplateCase) -> list[str]:
    """The current list is the chart's, word for word, and holds nothing changed today.

    A medication the client reports taking is allowed after it, marked as stated.
    """
    expected = case.expected.current_medications
    if expected is None:
        return []
    value = _value(draft, "medications", "current_medications")
    items = [str(i) for i in value] if isinstance(value, list) else [str(value or "")]
    listed = {normalize(i) for i in items if i.strip().lower() not in MEDICATION_HEADINGS}
    wanted = {normalize(line) for line in expected}
    problems = [
        f"medications.current_medications: {line!r} is on the chart but not listed as written"
        for line in expected
        if normalize(line) not in listed
    ]
    problems += [
        f"medications.current_medications: {item!r} is not on the chart"
        for item in items
        if item.strip()
        and item.strip().lower() not in MEDICATION_HEADINGS
        and normalize(item) not in wanted
        and not is_stated_this_visit(item)
    ]
    problems += [
        f"medications.current_medications: {word!r} was changed this visit; it belongs to the plan"
        for word in case.expected.not_current
        if any(word in normalize(i).split() for i in items)
    ]
    return problems


def history_from_chart(draft: Draft, case: TemplateCase) -> list[str]:
    """Each history field is the chart's text, word for word, or "Not recorded".

    What the visit changed or added may follow, as ``(stated this visit: ...)``.
    """
    recorded = {f.key: f.text for f in case.history}
    problems = []
    for group in HISTORY_GROUPS:
        if group.key == SUBSTANCE_USE:
            continue
        for field in group.fields:
            text = _text(draft, group.key, field.key)
            path = f"{group.key}.{field.key}"
            chart_text, stated = split_stated(text)
            if stated is not None and not stated.strip():
                problems.append(f"{path}: an empty (stated this visit: ...) suffix")
            if field.key in recorded:
                if normalize(chart_text) != normalize(recorded[field.key]):
                    problems.append(f"{path}: not the chart's text as recorded")
            elif normalize(chart_text) != "not recorded":
                problems.append(f'{path}: nothing on the chart, so it should read "Not recorded"')
    return problems


CHECKS: dict[str, Callable[[Draft, TemplateCase], list[str]]] = {
    "codes_only_dictated": codes_only_dictated,
    "psychotherapy_section": psychotherapy_section,
    "risk_quoted": risk_quoted,
    "safety_plan": safety_plan_only_with_ideation,
    "pdmp_line": pdmp_line,
    "telehealth_attestation": telehealth_attestation,
    "substances": substances,
    "diagnoses_only_stated": diagnoses_only_stated,
    "measures_undated": measures_undated,
    "medications_from_chart": medications_from_chart,
    "history_from_chart": history_from_chart,
}


def grade(draft: Draft, case: TemplateCase) -> dict[str, list[str]]:
    """Every check's problems, by check name; all empty means the draft passes."""
    return {name: check(draft, case) for name, check in CHECKS.items()}
