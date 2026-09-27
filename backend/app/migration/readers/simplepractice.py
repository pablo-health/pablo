# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Read a SimplePractice "Export - Complete" archive into typed records.

The archive is a folder tree. Contact cards are vCards named
``<Display Name> - <id>.vcf``; every clinical and billing document is a PDF
under a folder named after the client's first and last name; the secure
message log is a text file; client uploads sit under ``Stored documents``.
The layout this module parses is the one captured in
``backend/tests/fixtures/simplepractice_export`` — read that folder's README
before changing anything here, and re-capture before trusting a change.

Two facts about the source shape every caller has to keep in mind:

* **Folders are first + last name only.** Two clients with one name share
  every folder. What tells them apart is the ``Client:`` line of a note
  (the full display name, middle initial included, as it stands at export
  time — except on administrative notes, which print first and last name
  only), the ``DOB:`` line when the client has a birthday, the contact
  card's *file name* (its body never carries a middle initial), and the
  email or phone in a billing document. Uploads carry nothing. The reader
  therefore records each document's display name and DOB as it found them
  and leaves attribution to the caller.
* **Text is read in position order.** PyMuPDF's default reading order does
  not follow the visual layout of these files; ``sort=True`` does, and the
  header of a note is a two-column table (appointment on the left,
  diagnoses on the right) that only makes sense read that way.

Everything here is pure: a directory in, dataclasses out, nothing else
touched.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import fitz  # type: ignore[import-untyped]  # PyMuPDF, as the engine imports it

SOURCE_SYSTEM = "simplepractice"

#: The zone the export prints its times in. Every timestamp in the archive is
#: labelled "ET"; nothing else has been observed, so an unknown label falls
#: back to Eastern rather than guessing a different zone from two letters.
_ZONES = {"ET": ZoneInfo("America/New_York")}
_DEFAULT_ZONE = _ZONES["ET"]

#: Depth of a path under a client folder: ``<top>/<client>/<file>``.
_CLIENT_FILE_DEPTH = 3
#: Depth of a billing document: ``Billing Documents/<client>/<kind>/<file>``.
_BILLING_FILE_DEPTH = 4
#: Header lines to scan for ``Client:`` / ``DOB:`` / ``Provider:``.
_HEADER_SCAN_LINES = 8
#: A vCard ADR has seven components; the street is the third.
_ADR_COMPONENTS = 7
#: A stamp with a zone label splits into text and label.
_STAMP_PARTS = 2

_CARD_NAME_RE = re.compile(r"^(?P<display>.+?) - (?P<id>\d+)\.vcf$")
_NOTE_NAME_RE = re.compile(
    r"^(?P<title>Progress Note|Psychotherapy Note|Chart Note|Administrative Note) "
    r"(?P<date>\d{4}-\d{2}-\d{2}) (?P<time>\d{6}) (?P<id>\d+)\.pdf$"
)
_PLAN_NAME_RE = re.compile(r"^Diagnosis and treatment plan - (?P<id>\d+)\.pdf$")
_QUESTIONNAIRE_NAME_RE = re.compile(r"^Questionnaire - (?P<code>[A-Z0-9-]+) - (?P<id>\d+)\.pdf$")
_UPLOAD_NAME_RE = re.compile(r"^(?P<ordinal>\d+)-(?P<name>.+)$")
_DIAGNOSIS_RE = re.compile(r"^(?P<code>[A-Z]\d{2}(?:\.\d{1,4})?) - (?P<text>.+)$")
_APPOINTMENT_RE = re.compile(r"^(?P<kind>.+?) appointment on (?P<date>[A-Z][a-z]+ \d{1,2}, \d{4})$")
_TIME_RANGE_RE = re.compile(
    r"^(?P<start>\d{1,2}:\d{2} [ap]m) - (?P<end>\d{1,2}:\d{2} [ap]m) (?P<zone>[A-Z]{2,4}), "
    r"(?P<minutes>\d+) min$"
)
_BILLING_CODE_RE = re.compile(r"^Billing code: (?P<code>\S+) - (?P<text>.+)$")
_STAMP = r"[A-Z][a-z]{2} \d{1,2}, \d{4} at \d{1,2}:\d{2} [ap]m [A-Z]{2,4}"
_CREATED_RE = re.compile(
    rf"Created on (?P<created>{_STAMP})\. Last updated on (?P<updated>{_STAMP})\."
)
_COMPLETED_RE = re.compile(rf"Completed on (?P<completed>{_STAMP})\.")
_PAGE_RE = re.compile(r"\bPage \d+ of \d+\s*$")
_LOCKED_RE = re.compile(rf"^Locked and Signed by (?P<name>.+?) on (?P<when>{_STAMP})\.$")
_SIGNED_BY_RE = re.compile(r"^Signed by (?P<name>.+)$")
_SIGNED_AT_RE = re.compile(
    r"^(?P<when>[A-Z][a-z]+ \d{1,2}, \d{4} at \d{1,2}:\d{2} [ap]m) \((?P<zone>[A-Z]{2,4})\)$"
)
_NOTED_ON_RE = re.compile(
    r"^Noted On:\s+(?P<when>[A-Z][a-z]+ \d{1,2}, \d{4} at \d{1,2}:\d{2} [ap]m) "
    r"\((?P<zone>[A-Z]{2,4})\)$"
)
# The score sits at the left of a line whose right column is the interpretation.
_SCORE_RE = re.compile(r"^(?P<score>\d+) \((?P<severity>[A-Za-z ]+)\)")
_NUMBER_RE = re.compile(r"(?:^|\s)#(?P<n>\d+)(?:\s|$)")
_ITEM_RE = re.compile(
    r"^(?P<n>\d+)\.\s+(?P<question>.+?)\s{2,}(?P<response>.+?)\s{2,}(?P<score>\d)/(?P<max>\d)\b"
)
_DAY_HEADER_RE = re.compile(r"^----- (?P<day>[A-Z][a-z]+ \d{1,2}, \d{4}) -----$")
_SENDER_RE = re.compile(
    r"^(?P<sender>.+?) \[(?P<time>\d{1,2}:\d{2} [ap]m) \((?P<zone>[A-Z]{2,4})\)\]$"
)
_ISSUED_RE = re.compile(r"Issued: (?P<date>\d{2}/\d{2}/\d{4})")
_MONEY_RE = re.compile(r"\$?(?P<amount>-?\d[\d,]*\.\d{2})")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_TO_RE = re.compile(r"^(Bill )?To(\s|$)")
_SERVICE_LINE_RE = re.compile(
    r"^(?P<date>\d{2}/\d{2}/\d{4})\s+(?:(?P<pos>\d{2})\s+)?(?P<code>\d{5})\s+(?P<dx>\d+)\s+"
    r"(?P<desc>.+?)\s{2,}(?P<units>\d+)\s+\$(?P<fee>[\d,]+)\s+\$(?P<paid>[\d,]+)\s*$"
)

_NOTE_TITLES = {
    "Progress Note": "progress",
    "Psychotherapy Note": "psychotherapy",
    "Chart Note": "chart",
    "Administrative Note": "administrative",
}
_PLAN_TITLE = "Diagnosis and treatment plan"
_NOTE_TOPS = {"Medical Records", "Psychotherapy Notes", "Administrative"}
_BILLING_KINDS = {
    "Invoices": "invoice",
    "Statements": "statement",
    "Statements for Insurance Reimbursements": "superbill",
}


# --------------------------------------------------------------------------- records


@dataclass(frozen=True)
class Address:
    street: str
    city: str
    state: str
    postal_code: str


@dataclass(frozen=True)
class ContactCard:
    """One vCard. ``display_name`` is taken from the FILE NAME, which is the
    only place the export writes a middle initial; ``formatted_name`` is the
    card's own FN, which never carries one."""

    source_id: str
    display_name: str
    formatted_name: str
    given_name: str
    family_name: str
    email: str | None
    phone: str | None
    address: Address | None
    birthday: date | None
    path: str
    digest: str

    @property
    def folder_name(self) -> str:
        """The name the export uses for this client's folders."""
        return f"{self.given_name} {self.family_name}".strip()


@dataclass(frozen=True)
class Diagnosis:
    code: str
    description: str


@dataclass(frozen=True)
class AppointmentInfo:
    kind: str
    on: date
    start: time
    end: time
    duration_minutes: int
    zone: str
    billing_code: str | None
    billing_description: str | None

    @property
    def starts_at(self) -> datetime:
        return datetime.combine(self.on, self.start, tzinfo=_zone(self.zone))

    @property
    def ends_at(self) -> datetime:
        return datetime.combine(self.on, self.end, tzinfo=_zone(self.zone))


@dataclass(frozen=True)
class NoteRecord:
    """A note PDF: one of progress, psychotherapy, chart, administrative or
    treatment_plan. ``body`` is the text under the title, with the PDF's
    line wrapping undone and the author's paragraph breaks kept."""

    kind: str
    source_id: str
    path: str
    client_display_name: str
    client_dob: date | None
    provider_name: str | None
    appointment: AppointmentInfo | None
    noted_at: datetime | None
    diagnoses: tuple[Diagnosis, ...]
    title: str
    body: str
    signed_by: str | None
    signed_at: datetime | None
    locked: bool
    created_at: datetime | None
    updated_at: datetime | None
    digest: str


@dataclass(frozen=True)
class ItemScore:
    number: int
    question: str
    response: str
    score: int
    max_score: int


@dataclass(frozen=True)
class QuestionnaireRecord:
    source_id: str
    path: str
    client_display_name: str
    client_dob: date | None
    provider_name: str | None
    instrument: str
    title: str
    total_score: int | None
    severity: str | None
    items: tuple[ItemScore, ...]
    completed_at: datetime | None
    digest: str


@dataclass(frozen=True)
class ServiceLine:
    on: date
    code: str
    dx_pointer: str
    description: str
    units: int
    fee_cents: int
    paid_cents: int


@dataclass(frozen=True)
class BillingDocument:
    """An invoice, statement or superbill. Read for identity and totals; the
    lines are kept so a later phase can land them as ledger entries."""

    kind: str
    source_id: str
    path: str
    client_folder: str
    client_display_name: str | None
    client_email: str | None
    client_phone: str | None
    client_dob: date | None
    provider_name: str | None
    provider_email: str | None
    number: str | None
    issued_on: date | None
    total_cents: int | None
    balance_cents: int | None
    service_lines: tuple[ServiceLine, ...]
    digest: str


@dataclass(frozen=True)
class Message:
    sender: str
    sent_at: datetime | None
    body: str


@dataclass(frozen=True)
class MessageThread:
    source_id: str
    path: str
    participants: tuple[str, ...]
    messages: tuple[Message, ...]
    digest: str


@dataclass(frozen=True)
class Upload:
    source_id: str
    path: str
    client_folder: str
    ordinal: int
    original_filename: str
    size_bytes: int
    digest: str


@dataclass(frozen=True)
class SimplePracticeArchive:
    root: str
    contacts: tuple[ContactCard, ...] = ()
    notes: tuple[NoteRecord, ...] = ()
    questionnaires: tuple[QuestionnaireRecord, ...] = ()
    billing: tuple[BillingDocument, ...] = ()
    threads: tuple[MessageThread, ...] = ()
    uploads: tuple[Upload, ...] = ()
    unrecognized: tuple[str, ...] = ()
    unreadable: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def provider_names(self) -> tuple[str, ...]:
        names = {n.provider_name for n in self.notes if n.provider_name}
        names |= {q.provider_name for q in self.questionnaires if q.provider_name}
        names |= {b.provider_name for b in self.billing if b.provider_name}
        return tuple(sorted(names))

    @property
    def client_folders(self) -> tuple[str, ...]:
        folders = {n.path.split("/")[1] for n in self.notes}
        folders |= {q.path.split("/")[1] for q in self.questionnaires}
        folders |= {b.client_folder for b in self.billing}
        folders |= {u.client_folder for u in self.uploads}
        return tuple(sorted(folders))


# --------------------------------------------------------------------------- helpers


def _zone(label: str) -> ZoneInfo:
    return _ZONES.get(label, _DEFAULT_ZONE)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_us_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%m/%d/%Y").date()
    except ValueError:
        return None


def _parse_long_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%B %d, %Y").date()
    except ValueError:
        return None


def _parse_stamp(value: str) -> datetime | None:
    """``Sep 26, 2026 at 11:30 pm ET`` or ``September 27, 2026 at 10:44 am``."""
    value = value.strip()
    zone = _DEFAULT_ZONE
    parts = value.rsplit(" ", 1)
    if len(parts) == _STAMP_PARTS and parts[1].isalpha() and parts[1].isupper():
        value, label = parts
        zone = _zone(label)
    for fmt in ("%b %d, %Y at %I:%M %p", "%B %d, %Y at %I:%M %p"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=zone)
        except ValueError:
            continue
    return None


def _parse_clock(value: str) -> time | None:
    try:
        return datetime.strptime(value, "%I:%M %p").time()
    except ValueError:
        return None


def _cents(text: str) -> int | None:
    m = _MONEY_RE.search(text)
    if not m:
        return None
    return round(float(m.group("amount").replace(",", "")) * 100)


def _pdf_lines(data: bytes) -> list[str]:
    """The PDF's text in position order, one entry per printed line.

    Position sorting is what makes a two-column header read row by row.
    Repeated page footers are kept: the parser recognises them wherever
    they fall.
    """
    with fitz.open(stream=data, filetype="pdf") as doc:
        text = "\n".join(page.get_text("text", sort=True) for page in doc)
    return text.splitlines()


def _header_value(lines: list[str], label: str) -> str | None:
    prefix = f"{label}:"
    for line in lines[:_HEADER_SCAN_LINES]:
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped[len(prefix) :].strip()
            return value or None
    return None


def _find_title(lines: list[str], titles: set[str]) -> int | None:
    for i, line in enumerate(lines):
        if line.strip() in titles:
            return i
    return None


def _is_footer(line: str) -> bool:
    stripped = line.strip()
    return bool(
        _CREATED_RE.search(stripped)
        or _COMPLETED_RE.search(stripped)
        or _LOCKED_RE.match(stripped)
        or _PAGE_RE.search(stripped)
    )


# --------------------------------------------------------------------------- contacts


def _read_card(path: Path, rel: str) -> ContactCard | None:
    m = _CARD_NAME_RE.match(path.name)
    if not m:
        return None
    data = path.read_bytes()
    fields: dict[str, str] = {}
    for raw in data.decode("utf-8", errors="replace").splitlines():
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        fields[key.split(";")[0].upper()] = value.strip()
    n_parts = fields.get("N", "").split(";")
    family = n_parts[0] if n_parts else ""
    given = n_parts[1] if len(n_parts) > 1 else ""
    address: Address | None = None
    if fields.get("ADR"):
        a = fields["ADR"].split(";")
        a += [""] * (_ADR_COMPONENTS - len(a))
        if any(a[2:6]):
            address = Address(street=a[2], city=a[3], state=a[4], postal_code=a[5])
    birthday: date | None = None
    if fields.get("BDAY"):
        try:
            birthday = datetime.strptime(fields["BDAY"], "%Y%m%d").date()
        except ValueError:
            birthday = None
    return ContactCard(
        source_id=m.group("id"),
        display_name=m.group("display"),
        formatted_name=fields.get("FN", ""),
        given_name=given,
        family_name=family,
        email=fields.get("EMAIL") or None,
        phone=fields.get("TEL") or None,
        address=address,
        birthday=birthday,
        path=rel,
        digest=_digest(data),
    )


# --------------------------------------------------------------------------- notes


def _split_header(lines: list[str], title_index: int) -> tuple[list[str], list[str]]:
    """Split the header block above the title into left (appointment) and
    right (diagnosis) columns, using the ``Diagnosis:`` label's column."""
    header = lines[:title_index]
    dx_col: int | None = None
    for line in header:
        col = line.find("Diagnosis:")
        if col >= 0:
            dx_col = col
            break
    if dx_col is None:
        return [line.strip() for line in header if line.strip()], []
    left: list[str] = []
    right: list[str] = []
    for line in header:
        l_part = line[:dx_col].strip()
        r_part = line[dx_col:].strip()
        if l_part:
            left.append(l_part)
        if r_part:
            right.append(r_part.removeprefix("Diagnosis:").strip())
    return left, [r for r in right if r]


def _parse_diagnoses(right: list[str]) -> tuple[Diagnosis, ...]:
    out: list[Diagnosis] = []
    for chunk in right:
        m = _DIAGNOSIS_RE.match(chunk)
        if m:
            out.append(Diagnosis(code=m.group("code"), description=m.group("text")))
        elif out:
            # A wrapped continuation of the previous description.
            last = out.pop()
            out.append(Diagnosis(code=last.code, description=f"{last.description} {chunk}"))
    return tuple(out)


def _parse_appointment(left: list[str]) -> AppointmentInfo | None:
    kind: str | None = None
    on: date | None = None
    start = end = None
    minutes: int | None = None
    zone = "ET"
    code = desc = None
    for chunk in left:
        text = chunk.removeprefix("Appointment:").strip()
        if m := _APPOINTMENT_RE.match(text):
            kind, on = m.group("kind"), _parse_long_date(m.group("date"))
        elif m := _TIME_RANGE_RE.match(text):
            start, end = _parse_clock(m.group("start")), _parse_clock(m.group("end"))
            minutes, zone = int(m.group("minutes")), m.group("zone")
        elif m := _BILLING_CODE_RE.match(text):
            code, desc = m.group("code"), m.group("text")
    if kind is None or on is None or start is None or end is None or minutes is None:
        return None
    return AppointmentInfo(
        kind=kind,
        on=on,
        start=start,
        end=end,
        duration_minutes=minutes,
        zone=zone,
        billing_code=code,
        billing_description=desc,
    )


@dataclass(frozen=True)
class _Footer:
    created: datetime | None
    updated: datetime | None
    locked: bool
    locked_by: str | None
    locked_at: datetime | None


def _parse_footer(lines: list[str]) -> _Footer:
    created = updated = locked_at = None
    locked, locked_by = False, None
    for line in lines:
        stripped = line.strip()
        if m := _CREATED_RE.search(stripped):
            created, updated = _parse_stamp(m.group("created")), _parse_stamp(m.group("updated"))
        elif m := _LOCKED_RE.match(stripped):
            locked, locked_by, locked_at = True, m.group("name"), _parse_stamp(m.group("when"))
    return _Footer(created, updated, locked, locked_by, locked_at)


def _parse_signature(lines: list[str]) -> tuple[str | None, datetime | None]:
    """The block after a body on a locked note: ``Provider`` / name /
    ``Signed by <name>`` / timestamp / ``IP address: ...``. The IP address
    is read past and never returned — it is the source system's evidence,
    not ours."""
    signed_by = None
    signed_at = None
    for line in lines:
        stripped = line.strip()
        if m := _SIGNED_BY_RE.match(stripped):
            signed_by = m.group("name")
        elif m := _SIGNED_AT_RE.match(stripped):
            signed_at = _parse_stamp(f"{m.group('when')} {m.group('zone')}")
    return signed_by, signed_at


def _note_body(lines: list[str], title_index: int) -> tuple[str, list[str], list[str]]:
    """Everything after the title up to the signature block or footer.

    Returns the body as text (the PDF's wrapping undone — those breaks are
    the renderer's, not the author's — with blank-line paragraph breaks
    kept), the raw body lines, and the tail lines after the body.
    """
    raw: list[str] = []
    tail_start = len(lines)
    for i in range(title_index + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped == "Provider" or _is_footer(lines[i]) or _SIGNED_BY_RE.match(stripped):
            tail_start = i
            break
        raw.append(lines[i].rstrip())
    while raw and not raw[-1].strip():
        raw.pop()
    while raw and not raw[0].strip():
        raw.pop(0)
    paragraphs: list[str] = []
    current: list[str] = []
    for line in raw:
        if line.strip():
            current.append(line.strip())
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs), raw, lines[tail_start:]


def _read_note(path: Path, rel: str, kind: str, source_id: str) -> NoteRecord:
    data = path.read_bytes()
    lines = _pdf_lines(data)
    titles = {_PLAN_TITLE} if kind == "treatment_plan" else set(_NOTE_TITLES)
    title_index = _find_title(lines, titles)
    if title_index is None:
        raise ValueError(f"no title line found in {rel}")
    left, right = _split_header(lines, title_index)
    noted_at: datetime | None = None
    for chunk in left:
        if m := _NOTED_ON_RE.match(chunk):
            noted_at = _parse_stamp(f"{m.group('when')} {m.group('zone')}")
    body, raw_body, tail = _note_body(lines, title_index)
    signed_by, signed_at = _parse_signature(tail)
    footer = _parse_footer(tail)
    if footer.locked and not signed_by:
        signed_by, signed_at = footer.locked_by, footer.locked_at
    diagnoses = _parse_diagnoses(right)
    if kind == "treatment_plan":
        # The plan lists its diagnoses under a "Diagnosis" heading in the body.
        dx_lines = [line.strip() for line in raw_body]
        if dx_lines and dx_lines[0] == "Diagnosis":
            diagnoses = _parse_diagnoses([line for line in dx_lines[1:] if line])
    return NoteRecord(
        kind=kind,
        source_id=source_id,
        path=rel,
        client_display_name=_header_value(lines, "Client") or "",
        client_dob=_parse_us_date(_header_value(lines, "DOB") or ""),
        provider_name=_header_value(lines, "Provider"),
        appointment=_parse_appointment(left),
        noted_at=noted_at,
        diagnoses=diagnoses,
        title=lines[title_index].strip(),
        body=body,
        signed_by=signed_by,
        signed_at=signed_at,
        locked=footer.locked,
        created_at=footer.created,
        updated_at=footer.updated,
        digest=_digest(data),
    )


# --------------------------------------------------------------------------- questionnaires


def _read_questionnaire(path: Path, rel: str, code: str, source_id: str) -> QuestionnaireRecord:
    data = path.read_bytes()
    lines = _pdf_lines(data)
    title = next(
        (line.strip() for line in lines if re.fullmatch(r"[A-Z]{3,5}-\d{1,2}", line.strip())), code
    )
    total: int | None = None
    severity: str | None = None
    items: list[ItemScore] = []
    completed: datetime | None = None
    for line in lines:
        stripped = line.strip()
        if total is None and (m := _SCORE_RE.match(stripped)):
            total, severity = int(m.group("score")), m.group("severity")
        elif m := _ITEM_RE.match(stripped):
            items.append(
                ItemScore(
                    number=int(m.group("n")),
                    question=m.group("question").strip(),
                    response=m.group("response").strip(),
                    score=int(m.group("score")),
                    max_score=int(m.group("max")),
                )
            )
        elif m := _COMPLETED_RE.search(stripped):
            completed = _parse_stamp(m.group("completed"))
    return QuestionnaireRecord(
        source_id=source_id,
        path=rel,
        client_display_name=_header_value(lines, "Client") or "",
        client_dob=_parse_us_date(_header_value(lines, "DOB") or ""),
        provider_name=_header_value(lines, "Provider"),
        instrument=title.lower().replace("-", ""),
        title=title,
        total_score=total,
        severity=severity,
        items=tuple(items),
        completed_at=completed,
        digest=_digest(data),
    )


# --------------------------------------------------------------------------- billing


@dataclass(frozen=True)
class _BillingIdentity:
    client_name: str | None
    client_email: str | None
    client_phone: str | None
    client_dob: date | None
    provider_name: str | None
    provider_email: str | None


def _billing_identity(stripped: list[str]) -> _BillingIdentity:
    # Invoices head the block "Bill To", statements and superbills just "To";
    # the right column of that line holds the document's own title. The
    # provider's email prints in the right column before the client's, which
    # follows the client's phone in the left column.
    client_name = None
    for i, s in enumerate(stripped):
        if _TO_RE.match(s) and i + 1 < len(stripped):
            client_name = stripped[i + 1].split("  ")[0].strip() or None
            break
    emails = _EMAIL_RE.findall("\n".join(stripped))
    phone_line = next(
        (s for s in stripped if re.fullmatch(r"\d{10}", s.split("  ")[0].strip())), None
    )
    dob = None
    for s in stripped:
        if s.startswith("DOB:"):
            dob = _parse_us_date(s.removeprefix("DOB:").strip().split(" ")[0])
    provider_name = None
    for i, s in enumerate(stripped):
        if s == "From" and i + 1 < len(stripped):
            provider_name = stripped[i + 1] or None
            break
    return _BillingIdentity(
        client_name=client_name,
        client_email=emails[1] if len(emails) > 1 else None,
        client_phone=phone_line.split("  ")[0].strip() if phone_line else None,
        client_dob=dob,
        provider_name=provider_name,
        provider_email=emails[0] if emails else None,
    )


def _billing_totals(stripped: list[str]) -> tuple[int | None, int | None]:
    total = balance = None
    for i, s in enumerate(stripped):
        if s.startswith("Total") and not s.startswith("Total Paid"):
            total = _cents(s)
        elif s.startswith(("Ending Balance", "Balance")):
            balance = _cents(s)
            if balance is None and i + 1 < len(stripped):
                balance = _cents(stripped[i + 1])
    return total, balance


def _service_lines(stripped: list[str]) -> tuple[ServiceLine, ...]:
    out: list[ServiceLine] = []
    for s in stripped:
        m = _SERVICE_LINE_RE.match(s)
        if not m:
            continue
        on = _parse_us_date(m.group("date"))
        if on is None:
            continue
        out.append(
            ServiceLine(
                on=on,
                code=m.group("code"),
                dx_pointer=m.group("dx"),
                description=m.group("desc").strip(),
                units=int(m.group("units")),
                fee_cents=int(m.group("fee").replace(",", "")) * 100,
                paid_cents=int(m.group("paid").replace(",", "")) * 100,
            )
        )
    return tuple(out)


def _read_billing(path: Path, rel: str, kind: str, client_folder: str) -> BillingDocument:
    data = path.read_bytes()
    stripped = [line.strip() for line in _pdf_lines(data)]
    number = next((m.group("n") for s in stripped if (m := _NUMBER_RE.search(s))), None)
    issued = None
    for s in stripped:
        if m := _ISSUED_RE.search(s):
            issued = _parse_us_date(m.group("date"))
            break
    who = _billing_identity(stripped)
    total, balance = _billing_totals(stripped)
    return BillingDocument(
        kind=kind,
        source_id=f"{kind}:{client_folder}/{path.stem}",
        path=rel,
        client_folder=client_folder,
        client_display_name=who.client_name,
        client_email=who.client_email,
        client_phone=who.client_phone,
        client_dob=who.client_dob,
        provider_name=who.provider_name,
        provider_email=who.provider_email,
        number=number,
        issued_on=issued,
        total_cents=total,
        balance_cents=balance,
        service_lines=_service_lines(stripped),
        digest=_digest(data),
    )


# --------------------------------------------------------------------------- messages


def _read_thread(path: Path, rel: str) -> MessageThread:
    data = path.read_bytes()
    text = data.decode("utf-8", errors="replace")
    day: date | None = None
    messages: list[Message] = []
    participants: list[str] = []
    current: tuple[str, datetime | None] | None = None
    body: list[str] = []

    def flush() -> None:
        nonlocal current, body
        if current is not None:
            messages.append(
                Message(sender=current[0], sent_at=current[1], body="\n".join(body).strip())
            )
        current, body = None, []

    for raw in text.splitlines():
        line = raw.rstrip()
        if m := _DAY_HEADER_RE.match(line.strip()):
            flush()
            day = _parse_long_date(m.group("day"))
            continue
        if m := _SENDER_RE.match(line.strip()):
            flush()
            sender = m.group("sender")
            clock = _parse_clock(m.group("time"))
            sent = (
                datetime.combine(day, clock, tzinfo=_zone(m.group("zone")))
                if day and clock
                else None
            )
            current = (sender, sent)
            if sender not in participants:
                participants.append(sender)
            continue
        if current is not None:
            body.append(line)
    flush()
    return MessageThread(
        source_id=path.stem,
        path=rel,
        participants=tuple(participants),
        messages=tuple(messages),
        digest=_digest(data),
    )


# --------------------------------------------------------------------------- archive


def _read_upload(path: Path, rel: str, client_folder: str) -> Upload:
    m = _UPLOAD_NAME_RE.match(path.name)
    data = path.read_bytes()
    return Upload(
        source_id=rel,
        path=rel,
        client_folder=client_folder,
        ordinal=int(m.group("ordinal")) if m else 0,
        original_filename=m.group("name") if m else path.name,
        size_bytes=len(data),
        digest=_digest(data),
    )


def _read_client_document(path: Path, rel: str) -> NoteRecord | QuestionnaireRecord | None:
    if m := _NOTE_NAME_RE.match(path.name):
        return _read_note(path, rel, _NOTE_TITLES[m.group("title")], m.group("id"))
    if m := _PLAN_NAME_RE.match(path.name):
        return _read_note(path, rel, "treatment_plan", m.group("id"))
    if m := _QUESTIONNAIRE_NAME_RE.match(path.name):
        return _read_questionnaire(path, rel, m.group("code"), m.group("id"))
    return None


_Record = ContactCard | NoteRecord | QuestionnaireRecord | BillingDocument | MessageThread | Upload


def _read_entry(path: Path, rel: str, parts: tuple[str, ...]) -> _Record | None:
    """One file to one record, or ``None`` when the path is not an export shape."""
    top = parts[0]
    if top == "Contacts":
        return _read_card(path, rel)
    if top in _NOTE_TOPS and len(parts) == _CLIENT_FILE_DEPTH:
        return _read_client_document(path, rel)
    if (
        top == "Billing Documents"
        and len(parts) == _BILLING_FILE_DEPTH
        and path.suffix.lower() == ".pdf"
    ):
        kind = _BILLING_KINDS.get(parts[2])
        return _read_billing(path, rel, kind, parts[1]) if kind else None
    if top == "Secure messages" and path.suffix.lower() == ".txt":
        return _read_thread(path, rel)
    if top == "Stored documents" and len(parts) == _CLIENT_FILE_DEPTH:
        return _read_upload(path, rel, parts[1])
    return None


def read_simplepractice_archive(root: Path) -> SimplePracticeArchive:
    """Walk an export folder and return every record it recognises.

    Unrecognised files are listed, never guessed at; a PDF the parser cannot
    read is listed under ``unreadable`` with the reason. Neither stops the
    read: a run reports what it could not do and lands the rest.
    """
    root = Path(root)
    buckets: dict[type, list[_Record]] = {
        ContactCard: [],
        NoteRecord: [],
        QuestionnaireRecord: [],
        BillingDocument: [],
        MessageThread: [],
        Upload: [],
    }
    unrecognized: list[str] = []
    unreadable: list[tuple[str, str]] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name in {".DS_Store", "README.md"} or path.suffix == ".py":
            continue  # fixture housekeeping and OS noise, not part of an export
        rel = path.relative_to(root).as_posix()
        try:
            record = _read_entry(path, rel, path.relative_to(root).parts)
        except (ValueError, OSError) as exc:
            unreadable.append((rel, str(exc)))
            continue
        if record is None:
            unrecognized.append(rel)
        else:
            buckets[type(record)].append(record)
    return SimplePracticeArchive(
        root=str(root),
        contacts=tuple(c for c in buckets[ContactCard] if isinstance(c, ContactCard)),
        notes=tuple(n for n in buckets[NoteRecord] if isinstance(n, NoteRecord)),
        questionnaires=tuple(
            q for q in buckets[QuestionnaireRecord] if isinstance(q, QuestionnaireRecord)
        ),
        billing=tuple(b for b in buckets[BillingDocument] if isinstance(b, BillingDocument)),
        threads=tuple(t for t in buckets[MessageThread] if isinstance(t, MessageThread)),
        uploads=tuple(u for u in buckets[Upload] if isinstance(u, Upload)),
        unrecognized=tuple(unrecognized),
        unreadable=tuple(unreadable),
    )


__all__ = [
    "SOURCE_SYSTEM",
    "Address",
    "AppointmentInfo",
    "BillingDocument",
    "ContactCard",
    "Diagnosis",
    "ItemScore",
    "Message",
    "MessageThread",
    "NoteRecord",
    "QuestionnaireRecord",
    "ServiceLine",
    "SimplePracticeArchive",
    "Upload",
    "read_simplepractice_archive",
]
