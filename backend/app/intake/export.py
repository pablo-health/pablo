# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A form, and everything known about it, as one document somebody can keep.

What a practice needs when a form leaves the product: a chart copy, a record
for a release of information, an attachment to a referral. One file, opened
by any browser, printed by any printer.

Four decisions shape what is below, and each one is load-bearing.

**One file, and nothing fetched.** The style is inline, there is no script,
and nothing on the page loads anything when the page opens. A document that
fetches on load cannot be read on a laptop with no network, and an export
that reaches out is an export that can be told where to reach — so it does
neither. The one address in the file is the link beside each attached file,
which points back at the document route on the deployment this copy came
from: it is inert until somebody clicks it, and what it opens is behind the
sign-in the chart is behind. That is why the files themselves are not
printed into the page — a photograph of an insurance card embedded in every
copy of a document that gets filed and forwarded cannot be un-sent. The
page-break rules are the only concession to the printer, and they are the
reason this is HTML rather than text: a clinician prints it to PDF with what
the machine in front of them already has.

**Every value is escaped, and the escape is total.** Every string that came
from a patient, a practice or a document goes through :func:`html.escape`
before it is written, and the only unescaped markup in the result is markup
this module wrote itself. Prose a practice wrote in markdown is the one
exception, and it goes through
:func:`app.intake.documents.render_html`, which escapes first and marks up
second for the same reason.

**The same form renders the same bytes, apart from one line.** Nothing here
reads a clock, a locale or a dictionary's iteration order: the moment the
copy was taken is handed in, questions come in the order the form asks them,
answers come in the order they were written, and choices come in the order
the question offers them. So two exports of one form differ only in the
"Exported on" line, which is what makes the rest of the file comparable
against a copy somebody else was sent.

That line is there because a copy a practice files has to say when it was
taken — a chart copy with no date on it is a document nobody can place. It
is written in the practice's own timezone, with the zone named, and so is
every other moment on the page: the file is read by the practice that
produced it, and a clinician working out what 13:45 UTC was locally is being
asked to do arithmetic on their own record. Naming the zone is what keeps
that unambiguous for whoever it is forwarded to.

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
from html import escape
from typing import TYPE_CHECKING, Any, Literal

from ..outcome_measures.instruments import INSTRUMENT_REGISTRY, compute_total, severity_label
from .answers import CARD_SIDES
from .items import (
    ConsentDocumentConfig,
    DocumentRequestConfig,
    InstrumentConfig,
    InsuranceCardConfig,
    MultiChoiceConfig,
    NumberConfig,
    ScaleConfig,
    SectionConfig,
    SingleChoiceConfig,
    YesNoConfig,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime, tzinfo

    from .items import ItemConfig

#: What an answer is: the one that stands, one that was replaced by a later
#: answer, or one the form stopped asking for before it was handed in.
AnswerState = Literal["current", "replaced", "withheld"]

#: How every moment in the document is written: the practice's own frame
#: with the zone spelled out. The zone comes off the aware datetime rather
#: than off the machine, so two copies of one form still agree wherever
#: they are rendered — what varies is the practice, not the reader.
MOMENT_FORMAT = "%Y-%m-%d %H:%M %Z"

#: The "Exported on" line's own format. The zone is named in full beside
#: it rather than abbreviated, because this is the line that says which
#: frame the rest of the page is in.
EXPORTED_AT_FORMAT = "%Y-%m-%d %H:%M"

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
class ExportArtifact:
    """One file a question collected, as the document names it.

    No bytes and no thumbnail. An insurance card is a photograph of a
    government-adjacent document and a records request is somebody else's
    letter, and a file that prints itself into every copy of the export
    cannot be un-sent. What is here is enough to know the file exists and
    to go and get it: what it is called, how big it is, which question it
    answers and — on a card — which side.

    ``url`` points back at the clinician document route, which is behind
    the same sign-in the chart is. Following it is another read on the
    record; the link in a file that has left the product opens nothing by
    itself.
    """

    heading: str
    filename: str
    side: str | None
    size_bytes: int
    url: str


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
    #: When this copy was taken, and the frame every moment on it is
    #: written in. Both are the caller's: the clock is injected so a test
    #: can fix it, and the zone is the practice's own — the one the
    #: calendar and the claims already work in.
    exported_at: datetime
    timezone: tzinfo
    items: list[ExportItem] = field(default_factory=list)
    #: Every file the form collected, whatever question asked for it.
    artifacts: list[ExportArtifact] = field(default_factory=list)
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


def _insurance_card_lines(_config: InsuranceCardConfig, value: Mapping[str, object]) -> list[str]:
    """Which sides of the card arrived, in the order a card has them.

    The document ids in the stored value are not printed. An id is a
    handle into one deployment's storage and means nothing to whoever the
    file is forwarded to; what the files are called and where to get them
    is the attachments section's job.
    """
    attached = _document_entries(value)
    if not attached:
        return []
    sides = {str(entry.get("side")) for entry in attached}
    lines = [_CARD_SIDE_SENTENCES[side] for side in CARD_SIDES if side in sides]
    extra = len(attached) - len(lines)
    return lines + ([_file_count(extra)] if extra > 0 else [])


def _document_request_lines(
    _config: DocumentRequestConfig, value: Mapping[str, object]
) -> list[str]:
    """How many files answered this question. Their names are below."""
    attached = _document_entries(value)
    return [_file_count(len(attached))] if attached else []


def _document_entries(value: Mapping[str, object]) -> list[Mapping[str, object]]:
    """The entries an upload answer names, read defensively.

    JSONB, so what comes back is whatever was stored. A value that is not
    the shape the artifact route writes reads as naming no files, which
    prints as an unanswered question — the truthful direction for a
    document somebody files.
    """
    attached = value.get("documents")
    if not isinstance(attached, list):
        return []
    return [entry for entry in attached if isinstance(entry, dict)]


def _file_count(count: int) -> str:
    return "1 file sent." if count == 1 else f"{count} files sent."


#: What each side of a card reads as. Spelled out rather than capitalised
#: from the stored key, so the page says the same thing whatever the
#: column happens to hold.
_CARD_SIDE_SENTENCES: dict[str, str] = {
    "front": "Front of the card sent.",
    "back": "Back of the card sent.",
}


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
    DocumentRequestConfig: _document_request_lines,
    InstrumentConfig: _instrument_lines,
    InsuranceCardConfig: _insurance_card_lines,
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
    every time, on any machine. The clock and the zone are fields on the
    export rather than things this reads, which is what makes that true.
    """
    title = f"{export.packet_name} v{export.version} — {export.patient_name}"
    zone = export.timezone
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
        *_items(export.items, zone),
        *_artifacts(export.artifacts),
        *_signatures(export.signatures, zone),
        *_events(export.events, zone),
        "</body>",
        "</html>",
        "",
    ]
    return "\n".join(parts)


def _header(export: IntakeExport) -> list[str]:
    """Who this form belongs to, what has happened to it, and when it left."""
    zone = export.timezone
    parts = ["<header>"]
    if export.practice_name:
        parts.append(f"<p>{_e(export.practice_name)}</p>")
    parts.append(f"<h1>{_e(export.patient_name)}</h1>")
    parts.append(f'<p class="aside">{_e(_exported_on(export.exported_at, zone))}</p>')
    parts.append("<dl>")
    if export.patient_date_of_birth:
        parts += _entry("Date of birth", export.patient_date_of_birth)
    parts += _entry("Form", f"{export.packet_name} v{export.version}")
    parts += _entry("Status", STATUS_SENTENCES.get(export.status, export.status))
    parts += _moment_entry("Sent", export.assigned_at, zone)
    parts += _moment_entry("Handed in", export.submitted_at, zone)
    parts += _moment_entry("Accepted", export.accepted_at, zone)
    parts += _moment_entry("Withdrawn", export.withdrawn_at, zone)
    if export.receipt_code:
        parts += _entry("Receipt", export.receipt_code)
    parts.append("</dl>")
    parts.append("</header>")
    return parts


def _exported_on(moment: datetime, zone: tzinfo) -> str:
    """The one line two copies of a form are allowed to differ in.

    The zone is named in full rather than abbreviated, because this is the
    sentence the rest of the page's moments are read against and "EST" is
    two zones in some parts of the world.
    """
    written = moment.astimezone(zone).strftime(EXPORTED_AT_FORMAT)
    return f"Exported on {written} ({zone})"


def _items(items: Sequence[ExportItem], zone: tzinfo) -> list[str]:
    parts = ["<h2>Questions</h2>"]
    if not items:
        parts.append("<p>This form has no questions on it.</p>")
    for item in items:
        parts += _item(item, zone)
    return parts


def _item(item: ExportItem, zone: tzinfo) -> list[str]:
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
        parts += _answer(item.current, zone)

    if item.history:
        parts.append('<div class="earlier">')
        for earlier in item.history:
            parts += _answer(earlier, zone)
        parts.append("</div>")
    parts.append("</div>")
    return parts


def _answer(answer: ExportAnswer, zone: tzinfo) -> list[str]:
    """One answer's words, then who wrote it and when."""
    parts = [f'<p class="answer">{_e(line)}</p>' for line in answer.lines] or [
        f'<p class="answer">{_NO_ANSWER}</p>'
    ]
    aside = _aside(answer, zone)
    if aside:
        parts.append(f'<p class="aside">{_e(aside)}</p>')
    return parts


def _aside(answer: ExportAnswer, zone: tzinfo) -> str:
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
        parts.append(_moment(answer.written_at, zone))
    return " · ".join(parts)


def _artifacts(artifacts: Sequence[ExportArtifact]) -> list[str]:
    """The files the form collected, named and linked, never embedded.

    A form that asked for nothing gets no section, like the two below it.
    """
    if not artifacts:
        return []
    parts = ["<h2>Attached files</h2>"]
    for artifact in artifacts:
        parts += _artifact(artifact)
    return parts


def _artifact(artifact: ExportArtifact) -> list[str]:
    """One file: which question it answers, what it is, where to get it."""
    name = artifact.filename
    if artifact.side:
        name = f"{name} ({_CARD_SIDE_WORDS.get(artifact.side, artifact.side)})"
    return [
        '<div class="item">',
        f'<p class="question">{_e(artifact.heading)}</p>',
        f'<p class="answer">{_e(name)} · {_e(_file_size(artifact.size_bytes))}</p>',
        # The address is written out as well as linked: a printed page
        # cannot be clicked, and `evidence` is what wraps a long string
        # rather than running it off the edge of the paper.
        f'<p class="aside evidence"><a href="{_e(artifact.url)}">{_e(artifact.url)}</a></p>',
        "</div>",
    ]


#: How a card's side reads beside a filename.
_CARD_SIDE_WORDS: dict[str, str] = {"front": "front", "back": "back"}

#: The units a file size is written in, smallest first.
_SIZE_UNITS: tuple[str, ...] = ("bytes", "KB", "MB", "GB")

#: What each of those units is worth. Powers of 1024, which is what every
#: file manager the reader has ever used counts in.
_SIZE_STEP = 1024


def _file_size(size_bytes: int) -> str:
    """How big a file is, in the unit a person would say it in.

    One decimal place above bytes, rounded the same way every time, so two
    copies of one form agree.
    """
    size = float(size_bytes)
    for unit in _SIZE_UNITS[:-1]:
        if size < _SIZE_STEP:
            return f"{size_bytes} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= _SIZE_STEP
    return f"{size:.1f} {_SIZE_UNITS[-1]}"


def _signatures(signatures: Sequence[ExportSignature], zone: tzinfo) -> list[str]:
    """The evidence for each signature, or nothing at all.

    A form with no consent document on it gets no section, rather than a
    heading explaining its own absence.
    """
    if not signatures:
        return []
    parts = ["<h2>Signatures</h2>"]
    for signature in signatures:
        parts += _signature(signature, zone)
    return parts


def _signature(signature: ExportSignature, zone: tzinfo) -> list[str]:
    document = signature.document_title or "Document"
    if signature.document_version is not None:
        document = f"{document} v{signature.document_version}"
    parts = [
        '<div class="signature">',
        f'<p class="question">{_e(document)}</p>',
        "<dl>",
        *_entry("Signed by", f"{signature.signer_typed_name} ({signature.signer_role})"),
        *_moment_entry("Signed", signature.signed_at, zone),
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


def _events(events: Sequence[ExportEvent], zone: tzinfo) -> list[str]:
    if not events:
        return []
    parts = ["<h2>History</h2>"]
    for event in events:
        parts.append('<div class="event">')
        sentence = EVENT_SENTENCES.get(event.kind, event.kind)
        written = _moment(event.created_at, zone)
        parts.append(f'<p class="answer">{_e(sentence)} · {_e(written)}</p>')
        if event.note_to_patient:
            parts.append(f'<p class="aside">{_e(event.note_to_patient)}</p>')
        parts.append("</div>")
    return parts


def _entry(term: str, description: str, *, mono: bool = False) -> list[str]:
    css = ' class="evidence"' if mono else ""
    return [f"<dt>{_e(term)}</dt>", f"<dd{css}>{_e(description)}</dd>"]


def _moment_entry(term: str, moment: datetime | None, zone: tzinfo) -> list[str]:
    return [] if moment is None else _entry(term, _moment(moment, zone))


def _moment(moment: datetime, zone: tzinfo) -> str:
    """One moment in the practice's frame, with the zone spelled out."""
    return moment.astimezone(zone).strftime(MOMENT_FORMAT)


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
    "EXPORTED_AT_FORMAT",
    "MOMENT_FORMAT",
    "PROVENANCE_SENTENCES",
    "STATUS_SENTENCES",
    "AnswerState",
    "ExportAnswer",
    "ExportArtifact",
    "ExportEvent",
    "ExportItem",
    "ExportSignature",
    "IntakeExport",
    "answer_lines",
    "item_heading",
    "render",
]
