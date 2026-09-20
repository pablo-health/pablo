# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A form, and everything known about it, as one document somebody can keep.

What a practice needs when a form leaves the product: a chart copy, a record
for a release of information, an attachment to a referral. One file, opened
by any browser, printed by any printer.

Four decisions shape what is below, and each one is load-bearing.

**One file, and nothing fetched.** The style is inline, there is no script,
and no tag points at an address. A document that loads something from
somewhere cannot be read on a laptop with no network, and an export that
reaches out is an export that can be told where to reach — so it does
neither. The page-break rules are the only concession to the printer, and
they are the reason this is HTML rather than text: a clinician prints it to
PDF with what the machine in front of them already has.

**Every value is escaped, and the escape is total.** Every string that came
from a patient, a practice or a document goes through :func:`html.escape`
before it is written, and the only unescaped markup in the result is markup
this module wrote itself. Prose a practice wrote in markdown is the one
exception, and it goes through
:func:`app.intake.documents.render_html`, which escapes first and marks up
second for the same reason.

**The same form renders the same bytes.** Nothing here reads a clock, a
locale or a dictionary's iteration order: moments are written in UTC in one
fixed format, questions come in the order the form asks them, answers come
in the order they were written, and choices come in the order the question
offers them. So two exports of one form are byte-identical, which is what
makes the file comparable against a copy somebody else was sent. The
consequence is deliberate and worth stating: the document does not say when
it was produced. That fact belongs to the export, not to the form, and it
is on the audit log where an exported disclosure is recorded.

**It says what the rows say.** A question the patient was never shown reads
as not asked rather than as unanswered; an answer that was replaced is kept
under the question it answers with the answer that replaced it; an answer
withheld because the form stopped asking says so. None of those three is an
empty question, and a document that drew them all the same way would be
making a claim the rows do not support.

Scores are here, unlike on anything a patient reads: this is the clinician's
own copy of their own chart, and a measure without its total is a page of
numbers somebody has to add up.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC
from html import escape
from typing import TYPE_CHECKING, Any, Literal

from ..outcome_measures.instruments import INSTRUMENT_REGISTRY, compute_total, severity_label
from .items import (
    ConsentDocumentConfig,
    InstrumentConfig,
    MultiChoiceConfig,
    NumberConfig,
    ScaleConfig,
    SectionConfig,
    SingleChoiceConfig,
    YesNoConfig,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime

    from .items import ItemConfig

#: What an answer is: the one that stands, one that was replaced by a later
#: answer, or one the form stopped asking for before it was handed in.
AnswerState = Literal["current", "replaced", "withheld"]

#: How every moment in the document is written. UTC and unambiguous rather
#: than local and friendly: the file is read by whoever it was sent to, and
#: a timestamp that renders differently depending on the machine would make
#: two copies of one form disagree.
MOMENT_FORMAT = "%Y-%m-%d %H:%M UTC"

#: The sentence each status prints, matching the review screen's own words
#: so the file and the screen never describe a form differently.
STATUS_SENTENCES: dict[str, str] = {
    "assigned": "Sent to the patient.",
    "in_progress": "The patient has started this.",
    "submitted": "Handed in.",
    "needs_correction": "Sent back for corrections.",
    "accepted": "Accepted.",
    "withdrawn": "Withdrawn.",
}

#: What each review event says it was.
EVENT_SENTENCES: dict[str, str] = {
    "correction_requested": "Corrections requested",
    "corrected": "Patient sent corrections",
    "accepted": "Accepted",
    "clinician_entered": "Answer entered by practice",
}

#: What the engine's own questions are called, for the types a practice
#: does not write the wording for. The same headings the form builder
#: shows, so a printed form and the screen it was built on read alike.
ENGINE_HEADINGS: dict[str, str] = {
    "demographics": "Name and date of birth",
    "reason": "What brings you in",
    "emergency_contact": "Emergency contact",
    "guardian": "Parent or guardian",
    "instructions": "Instructions",
}

#: Where an answer came from, spelled out. A blank provenance prints
#: nothing — the row is old enough to predate the column rather than
#: authored by somebody unnamed, and inventing an author for it would be
#: the one claim this document must not make.
PROVENANCE_SENTENCES: dict[str, str] = {
    "patient": "Answered by the patient",
    "clinician": "Entered by the practice",
}

_NOT_ASKED = "Not asked."
_NO_ANSWER = "No answer."
_REPLACED = "Replaced"
_WITHHELD = "Withheld — the form stopped asking this"


@dataclass(frozen=True)
class ExportAnswer:
    """One answer, as the document prints it.

    ``lines`` is already rendered to words — "Yes", an option's label, a
    measure's total — because deciding what an answer says is a question
    about the item type, and doing it once here keeps the template free of
    every branch in the vocabulary.
    """

    lines: list[str]
    state: AnswerState
    provenance: str | None
    written_at: datetime | None


@dataclass(frozen=True)
class ExportItem:
    """One question, what it holds, and what it held before."""

    key: str
    item_type: str
    heading: str
    help_text: str | None
    #: Prose the form displays and collects nothing for, already safe HTML.
    body_html: str | None
    shown: bool
    required: bool
    current: ExportAnswer | None
    #: Oldest first. Answers that were replaced, and answers the form
    #: stopped asking for before it was handed in.
    history: list[ExportAnswer] = field(default_factory=list)


@dataclass(frozen=True)
class ExportSignature:
    """One signature's evidence, in full.

    Everything the row carries, including the address and the browser. The
    patient's own copy of the same signature leaves both behind; this is the
    practice's evidence record, and an evidence record that omits its
    evidence is a summary.
    """

    document_title: str | None
    document_version: int | None
    document_version_id: str
    document_digest: str
    signer_role: str
    signer_typed_name: str
    consent_statement: str
    consent_statement_version: str
    signed_at: datetime
    auth_strength: str
    session_id: str | None
    ip: str | None
    user_agent: str | None
    evidence_digest: str


@dataclass(frozen=True)
class ExportEvent:
    """One thing the practice or the patient did with the form."""

    kind: str
    created_at: datetime
    note_to_patient: str | None


@dataclass(frozen=True)
class IntakeExport:
    """Everything the document prints, already read out of the rows."""

    practice_name: str | None
    patient_name: str
    patient_date_of_birth: str | None
    packet_name: str
    version: int
    status: str
    receipt_code: str | None
    assigned_at: datetime | None
    submitted_at: datetime | None
    accepted_at: datetime | None
    withdrawn_at: datetime | None
    items: list[ExportItem] = field(default_factory=list)
    signatures: list[ExportSignature] = field(default_factory=list)
    events: list[ExportEvent] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Answers, in words
# ---------------------------------------------------------------------------


def answer_lines(config: ItemConfig | None, value: Mapping[str, object] | None) -> list[str]:
    """What one stored answer says, a line at a time.

    Empty when there is nothing stored. An item whose configuration no
    longer parses still prints what was saved against it, field by field, in
    key order: the question cannot be read back the way it was asked, and
    losing the answer as well would turn a broken question into a missing
    one.
    """
    if not value:
        return []
    if config is None:
        return _fields_of(value)
    return _lines_for(config, value)


def _lines_for(config: ItemConfig, value: Mapping[str, object]) -> list[str]:
    """Dispatch on the parsed question, with one reader per shape.

    The types with no reader of their own are the ones whose answer is a
    paragraph — what brings you in, a written answer, a contact block — and
    they share the last two branches rather than repeating one apiece.
    """
    reader = _READERS.get(type(config))
    if reader is not None:
        return reader(config, value)
    if config.item_type == "demographics":
        return _demographics_lines(value)
    text = value.get("text")
    if isinstance(text, str):
        return text.splitlines() or [text]
    return _fields_of(value)


def _demographics_lines(value: Mapping[str, object]) -> list[str]:
    """What the patient said about the name and date of birth on record.

    An attestation rather than an edit, so it reads as one: what they
    confirmed, what they flagged, and anything they wrote about it.
    """
    lines = [
        f"Name on record: {_confirmation(value.get('name_confirmed'))}",
        f"Date of birth on record: {_confirmation(value.get('dob_confirmed'))}",
    ]
    corrections = value.get("corrections")
    if isinstance(corrections, str) and corrections.strip():
        lines.append(f"Correction: {corrections}")
    return lines


def _confirmation(flag: object) -> str:
    if flag is True:
        return "confirmed"
    return "flagged as wrong" if flag is False else "not answered"


def _yes_no_lines(_config: YesNoConfig, value: Mapping[str, object]) -> list[str]:
    answered = value.get("yes")
    if not isinstance(answered, bool):
        return _fields_of(value)
    lines = ["Yes" if answered else "No"]
    follow_up = value.get("follow_up")
    if answered and isinstance(follow_up, str) and follow_up.strip():
        lines.extend(follow_up.splitlines())
    return lines


def _multi_choice_lines(config: MultiChoiceConfig, value: Mapping[str, object]) -> list[str]:
    """Every option that was picked, in the order the question offers them.

    The stored order is whatever order somebody clicked in, which is not a
    fact about the answer — and two exports of one form have to agree.
    """
    chosen = value.get("keys")
    if not isinstance(chosen, list):
        return _fields_of(value)
    picked = {str(key) for key in chosen}
    lines = [option.label for option in config.options if option.key in picked]
    unknown = sorted(picked - {option.key for option in config.options})
    return lines + unknown


def _single_choice_lines(config: SingleChoiceConfig, value: Mapping[str, object]) -> list[str]:
    """The wording the patient read, or the stored key when it is gone.

    A question can be re-worded between one version and the next, and the
    key is what survives; printing it is less useful than a label and more
    useful than nothing.
    """
    chosen = value.get("key")
    for option in config.options:
        if option.key == chosen:
            return [option.label]
    return [_scalar(chosen)]


def _scale_lines(config: ScaleConfig, value: Mapping[str, object]) -> list[str]:
    """The point, and the words at each end of the scale it sits on."""
    anchors = f"{config.min} is {config.min_label}, {config.max} is {config.max_label}"
    return [f"{_scalar(value.get('value'))} ({anchors})"]


def _number_lines(config: NumberConfig, value: Mapping[str, object]) -> list[str]:
    number = _scalar(value.get("value"))
    return [f"{number} {config.unit}" if config.unit else number]


def _consent_lines(_config: ConsentDocumentConfig, value: Mapping[str, object]) -> list[str]:
    """Whether it was signed. What was agreed to is in the signature block."""
    return ["Signed."] if value.get("signed") is True else []


def _instrument_lines(config: InstrumentConfig, value: Mapping[str, object]) -> list[str]:
    """A measure's total and band, then the answers it was computed from.

    The total is only printed when every item has a whole-number answer,
    because a total over some of the items is not this measure's score. A
    partial answer prints its items and no total, which is the truthful
    shape rather than a reassuring one.
    """
    raw = value.get("item_scores")
    if not isinstance(raw, dict):
        return _fields_of(value)

    definition = INSTRUMENT_REGISTRY[config.code]
    scores = {
        str(key): item
        for key, item in raw.items()
        if isinstance(item, int) and not isinstance(item, bool)
    }
    ordered = sorted(scores.items(), key=lambda pair: _item_order(pair[0]))
    answers = ", ".join(f"{key}: {item}" for key, item in ordered)

    if definition.valid_keys - set(scores):
        return [f"{definition.display_name}: not every question was answered.", answers]

    total = compute_total(definition, scores)
    band = severity_label(definition, total)
    headline = f"{definition.display_name}: {total} of {definition.max_total}"
    return [f"{headline} ({band})" if band else headline, answers]


def _item_order(key: str) -> tuple[int, str]:
    """A measure's item keys in numbered order, with anything else last."""
    return (int(key), "") if key.isdigit() else (1_000_000, key)


#: Which reader renders which kind of question. Keyed by the parsed config's
#: own class, so a new item type is a member in
#: :mod:`app.intake.items`, a reader here, and nothing else to remember.
_READERS: dict[type, Callable[[Any, Mapping[str, object]], list[str]]] = {
    ConsentDocumentConfig: _consent_lines,
    InstrumentConfig: _instrument_lines,
    MultiChoiceConfig: _multi_choice_lines,
    NumberConfig: _number_lines,
    ScaleConfig: _scale_lines,
    SingleChoiceConfig: _single_choice_lines,
    YesNoConfig: _yes_no_lines,
}


def _fields_of(value: Mapping[str, object]) -> list[str]:
    """Whatever is stored, field by field, in key order."""
    return [f"{key}: {_scalar(value[key])}" for key in sorted(value)]


def _scalar(value: object) -> str:
    """One stored value as text, written the same way every time."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return value
    if isinstance(value, list | dict):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", " "))
    return str(value)


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

_STYLE = """
:root { color-scheme: light; }
body { margin: 0 auto; max-width: 46rem; padding: 2rem 1.5rem;
  font-family: Georgia, "Times New Roman", serif; font-size: 12pt; line-height: 1.5;
  color: #1a1a1a; background: #fff; }
h1 { font-size: 18pt; margin: 0 0 0.25rem; }
h2 { font-size: 13pt; margin: 2rem 0 0.5rem; border-bottom: 1px solid #ccc;
  padding-bottom: 0.25rem; }
h3 { font-size: 12pt; margin: 1.5rem 0 0.25rem; }
p { margin: 0 0 0.5rem; }
dl { margin: 0 0 1rem; }
dt { font-size: 10pt; text-transform: uppercase; letter-spacing: 0.04em; color: #555; }
dd { margin: 0 0 0.4rem; }
.item { margin: 0 0 1.25rem; padding: 0 0 0.5rem; border-bottom: 1px dotted #ddd; }
.question { font-weight: bold; margin: 0 0 0.25rem; }
.answer { margin: 0 0 0.25rem; white-space: pre-wrap; }
.aside { font-size: 10pt; color: #555; margin: 0 0 0.25rem; }
.earlier { margin: 0.5rem 0 0 1rem; padding: 0 0 0 0.75rem; border-left: 2px solid #ddd; }
.evidence { font-family: "SFMono-Regular", Menlo, Consolas, monospace; font-size: 9pt;
  word-break: break-all; }
@media print {
  body { max-width: none; padding: 0; }
  .item, .signature, .event { break-inside: avoid; page-break-inside: avoid; }
  h2 { break-after: avoid; page-break-after: avoid; }
}
"""


def render(export: IntakeExport) -> str:
    """The whole document, as the bytes the route sends.

    Deterministic: the same :class:`IntakeExport` renders the same string,
    every time, on any machine.
    """
    title = f"{export.packet_name} v{export.version} — {export.patient_name}"
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width, initial-scale=1" />',
        f"<title>{_e(title)}</title>",
        f"<style>{_STYLE}</style>",
        "</head>",
        "<body>",
        *_header(export),
        *_items(export.items),
        *_signatures(export.signatures),
        *_events(export.events),
        "</body>",
        "</html>",
        "",
    ]
    return "\n".join(parts)


def _header(export: IntakeExport) -> list[str]:
    """Who this form belongs to and what has happened to it."""
    parts = ["<header>"]
    if export.practice_name:
        parts.append(f"<p>{_e(export.practice_name)}</p>")
    parts.append(f"<h1>{_e(export.patient_name)}</h1>")
    parts.append("<dl>")
    if export.patient_date_of_birth:
        parts += _entry("Date of birth", export.patient_date_of_birth)
    parts += _entry("Form", f"{export.packet_name} v{export.version}")
    parts += _entry("Status", STATUS_SENTENCES.get(export.status, export.status))
    parts += _moment_entry("Sent", export.assigned_at)
    parts += _moment_entry("Handed in", export.submitted_at)
    parts += _moment_entry("Accepted", export.accepted_at)
    parts += _moment_entry("Withdrawn", export.withdrawn_at)
    if export.receipt_code:
        parts += _entry("Receipt", export.receipt_code)
    parts.append("</dl>")
    parts.append("</header>")
    return parts


def _items(items: Sequence[ExportItem]) -> list[str]:
    parts = ["<h2>Questions</h2>"]
    if not items:
        parts.append("<p>This form has no questions on it.</p>")
    for item in items:
        parts += _item(item)
    return parts


def _item(item: ExportItem) -> list[str]:
    """One question: its heading, what it holds, and what it held before."""
    if item.item_type == "section":
        return [f"<h3>{_e(item.heading)}</h3>"]

    parts = ['<div class="item">']
    if item.heading:
        parts.append(f'<p class="question">{_e(item.heading)}</p>')
    if item.help_text:
        parts.append(f'<p class="aside">{_e(item.help_text)}</p>')
    if item.body_html is not None:
        parts.append(item.body_html)
    elif not item.shown:
        parts.append(f'<p class="answer">{_NOT_ASKED}</p>')
    elif item.current is None:
        parts.append(f'<p class="answer">{_NO_ANSWER}</p>')
    else:
        parts += _answer(item.current)

    if item.history:
        parts.append('<div class="earlier">')
        for earlier in item.history:
            parts += _answer(earlier)
        parts.append("</div>")
    parts.append("</div>")
    return parts


def _answer(answer: ExportAnswer) -> list[str]:
    """One answer's words, then who wrote it and when."""
    parts = [f'<p class="answer">{_e(line)}</p>' for line in answer.lines] or [
        f'<p class="answer">{_NO_ANSWER}</p>'
    ]
    aside = _aside(answer)
    if aside:
        parts.append(f'<p class="aside">{_e(aside)}</p>')
    return parts


def _aside(answer: ExportAnswer) -> str:
    """The line under an answer: what became of it, who wrote it, when."""
    parts: list[str] = []
    if answer.state == "replaced":
        parts.append(_REPLACED)
    elif answer.state == "withheld":
        parts.append(_WITHHELD)
    provenance = PROVENANCE_SENTENCES.get(answer.provenance or "")
    if provenance:
        parts.append(provenance)
    if answer.written_at is not None:
        parts.append(_moment(answer.written_at))
    return " · ".join(parts)


def _signatures(signatures: Sequence[ExportSignature]) -> list[str]:
    """The evidence for each signature, or nothing at all.

    A form with no consent document on it gets no section, rather than a
    heading explaining its own absence.
    """
    if not signatures:
        return []
    parts = ["<h2>Signatures</h2>"]
    for signature in signatures:
        parts += _signature(signature)
    return parts


def _signature(signature: ExportSignature) -> list[str]:
    document = signature.document_title or "Document"
    if signature.document_version is not None:
        document = f"{document} v{signature.document_version}"
    parts = [
        '<div class="signature">',
        f'<p class="question">{_e(document)}</p>',
        "<dl>",
        *_entry("Signed by", f"{signature.signer_typed_name} ({signature.signer_role})"),
        *_moment_entry("Signed", signature.signed_at),
        *_entry("Agreed to", signature.consent_statement),
        *_entry("Wording version", signature.consent_statement_version),
        *_entry("Identity check", signature.auth_strength),
    ]
    if signature.ip:
        parts += _entry("Address", signature.ip)
    if signature.user_agent:
        parts += _entry("Browser", signature.user_agent)
    if signature.session_id:
        parts += _entry("Session", signature.session_id, mono=True)
    parts += _entry("Document version", signature.document_version_id, mono=True)
    parts += _entry("Document digest", signature.document_digest, mono=True)
    parts += _entry("Evidence digest", signature.evidence_digest, mono=True)
    parts += ["</dl>", "</div>"]
    return parts


def _events(events: Sequence[ExportEvent]) -> list[str]:
    if not events:
        return []
    parts = ["<h2>History</h2>"]
    for event in events:
        parts.append('<div class="event">')
        sentence = EVENT_SENTENCES.get(event.kind, event.kind)
        parts.append(f'<p class="answer">{_e(sentence)} · {_e(_moment(event.created_at))}</p>')
        if event.note_to_patient:
            parts.append(f'<p class="aside">{_e(event.note_to_patient)}</p>')
        parts.append("</div>")
    return parts


def _entry(term: str, description: str, *, mono: bool = False) -> list[str]:
    css = ' class="evidence"' if mono else ""
    return [f"<dt>{_e(term)}</dt>", f"<dd{css}>{_e(description)}</dd>"]


def _moment_entry(term: str, moment: datetime | None) -> list[str]:
    return [] if moment is None else _entry(term, _moment(moment))


def _moment(moment: datetime) -> str:
    """One moment, in UTC, written the same way wherever it is read."""
    return moment.astimezone(UTC).strftime(MOMENT_FORMAT)


def _e(value: str) -> str:
    """Every string that reaches the document goes through here."""
    return escape(value, quote=True)


def item_heading(config: ItemConfig | None, label: str | None, key: str, item_type: str) -> str:
    """What a question is called on the page.

    The practice's own wording when there is any; a section's title when
    the practice wrote one there instead; a measure's own name; the
    engine's heading for the questions it asks itself. The item's key is
    the last resort, so a question is never nameless on a document somebody
    files.
    """
    if label and label.strip():
        return label
    if isinstance(config, SectionConfig):
        return config.title
    if isinstance(config, InstrumentConfig):
        return INSTRUMENT_REGISTRY[config.code].display_name or key
    return ENGINE_HEADINGS.get(item_type, key)


__all__ = [
    "ENGINE_HEADINGS",
    "EVENT_SENTENCES",
    "MOMENT_FORMAT",
    "PROVENANCE_SENTENCES",
    "STATUS_SENTENCES",
    "AnswerState",
    "ExportAnswer",
    "ExportEvent",
    "ExportItem",
    "ExportSignature",
    "IntakeExport",
    "answer_lines",
    "item_heading",
    "render",
]
