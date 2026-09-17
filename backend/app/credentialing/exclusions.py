# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Check a clinician against the federal exclusion lists.

A provider excluded under 42 USC 1320a-7 cannot be paid by any federal health
care programme, and no plan will panel one. It is the first thing a payer
checks and the cheapest thing for us to check first, because both lists are
public.

**What comes out of here is never a verdict about a person.** It is one of
three answers: nothing that looks like her, something that might be her, or we
could not look. The second is a job for an operator, not a banner on her
screen. That restraint is not politeness — the LEIE is a list of names, and the
arithmetic below says plainly how often a name is all it is.

THE ARITHMETIC, because it decides the design. Of the 84,001 rows in the
September 2026 LEIE, 8,882 carry an NPI — a shade over one in ten. So matching
on NPI alone would miss roughly nine exclusions in ten, and matching on name is
not a fallback but the main path. Names collide. Hence
:class:`MatchStrength`: an NPI hit is a fact, a name-and-date-of-birth hit is
close to one, and a bare name hit is a question.

TWO LISTS, TWO SHAPES, AND ONLY ONE OF THEM IS FREE.

* **LEIE** (HHS Office of Inspector General) publishes no API — only a CSV of
  the whole list, refreshed monthly. So this module parses and matches; it does
  not look anything up per clinician. Somebody upstream holds the file.
* **SAM.gov** (the government-wide exclusions list) does have an API, and it
  needs a key. A key is deployment configuration, so a deployment without one
  gets :attr:`Outcome.UNAVAILABLE` — never "clear". The distinction is the
  whole point: "we looked and found nothing" and "we could not look" are
  opposite answers that a careless design renders identically.

DATES MATTER TWICE. An exclusion has a start date and, if it ever ends, a
reinstatement date; and a check is a statement about the day it ran. Nothing
here caches an answer, because a clearance from a year ago is not a clearance.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

#: The whole current list, refreshed monthly by OIG. 15 MB and 84,000 rows as
#: of September 2026, which is why nothing here fetches it per clinician.
LEIE_DOWNLOAD_URL = "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv"

#: Version 4 of the SAM.gov exclusions endpoint. Needs ``api_key``.
SAM_BASE_URL = "https://api.sam.gov/entity-information/v4/exclusions"

#: Generous next to the NPPES timeout, and for the opposite reason: this does
#: not run inside a request a clinician is waiting on.
TIMEOUT_SECONDS = 30.0

#: OIG writes an absent NPI as ten zeroes rather than leaving the column empty,
#: and an absent date as eight. Neither is a value; both parse cleanly as one
#: if nothing says otherwise, which is how a placeholder becomes a match.
_ABSENT_NPI = "0000000000"
_ABSENT_DATE = "00000000"

#: The eighteen columns, in the order OIG publishes them. Taken from the file
#: itself rather than from the record-layout PDF, because the file is what we
#: parse.
LEIE_COLUMNS: tuple[str, ...] = (
    "LASTNAME",
    "FIRSTNAME",
    "MIDNAME",
    "BUSNAME",
    "GENERAL",
    "SPECIALTY",
    "UPIN",
    "NPI",
    "DOB",
    "ADDRESS",
    "CITY",
    "STATE",
    "ZIP",
    "EXCLTYPE",
    "EXCLDATE",
    "REINDATE",
    "WAIVERDATE",
    "WVRSTATE",
)

#: Two kinds of punctuation, treated differently, and the real file is what
#: decides which. OIG writes surnames as people spell them — 221 rows carry an
#: apostrophe and 1,488 a hyphen in the September 2026 file — so both have to
#: survive contact with however a clinician types her own name.
#:
#: An apostrophe or a full stop VANISHES, joining what it separated: "O'Brien"
#: and "OBrien" are one name. A hyphen becomes a SPACE, because
#: "Abad-Santos" and "Abad Santos" are also one name and joining them into
#: "AbadSantos" would match neither spelling anybody actually uses.
_ELIDED = re.compile(r"['`.]+")
_TO_SPACE = re.compile(r"[^A-Z]+")
_SUFFIXES = frozenset({"JR", "SR", "II", "III", "IV", "MD", "DO", "PHD", "LCSW"})


class Outcome(StrEnum):
    """What a check concluded. Three answers, and the third is not a failure."""

    #: Nothing on the list resembles her, as of the date checked.
    CLEAR = "clear"
    #: One or more rows might be her. A person decides; this module does not.
    POSSIBLE_MATCH = "possible_match"
    #: We could not look — no key, no file, or the service did not answer.
    #: Distinct from CLEAR by design, and never to be rendered as one.
    UNAVAILABLE = "unavailable"


class MatchStrength(StrEnum):
    """How much the hit actually proves, which differs a great deal."""

    #: The row carries her NPI. An NPI is unique to a provider.
    NPI = "npi"
    #: Name and date of birth both agree. Short of an identifier, this is as
    #: close as the list gets.
    NAME_AND_DOB = "name_and_dob"
    #: The name agrees and no date of birth was available on one side or the
    #: other. Common surnames make this weak evidence, and it is still worth
    #: surfacing, because an exclusion missed is worse than an hour wasted.
    NAME_ONLY = "name_only"


class ExclusionCheckError(Exception):
    """The list could not be reached or did not answer usefully.

    Raised rather than returned so a caller cannot mistake it for a result.
    Callers that want the three-way answer catch this and record
    :attr:`Outcome.UNAVAILABLE`.
    """


@dataclass(frozen=True)
class LeieRecord:
    """One row of the LEIE, with the placeholders resolved to ``None``.

    Individuals carry names and no ``business_name``; organisations the
    reverse. Both appear in the same file and this keeps them in one type,
    because a clinician who has incorporated can be excluded either way.
    """

    last_name: str | None
    first_name: str | None
    middle_name: str | None
    business_name: str | None
    general: str | None
    specialty: str | None
    npi: str | None
    date_of_birth: date | None
    city: str | None
    state: str | None
    exclusion_type: str | None
    excluded_on: date | None
    reinstated_on: date | None

    @property
    def is_currently_excluded(self) -> bool:
        """Whether this row still bites.

        OIG removes reinstated providers from the file, so in practice every
        row is current and this is belt and braces. It is here because the
        column exists, and a reinstatement we ignored would be us telling a
        clinician she is excluded when the government says she is not.
        """
        return self.reinstated_on is None


@dataclass(frozen=True)
class ExclusionMatch:
    """A row that might be her, and why we think so."""

    record: LeieRecord
    strength: MatchStrength


@dataclass(frozen=True)
class Subject:
    """The clinician being checked, in the only terms the lists understand.

    Every field is optional except the surname, because the lists are indexed
    by name and there is nothing to ask without one. The more of the rest we
    have, the fewer questions an operator is handed.
    """

    last_name: str
    first_name: str | None = None
    npi: str | None = None
    date_of_birth: date | None = None


@dataclass(frozen=True)
class ExclusionCheck:
    """One check of one list, on one day.

    ``source_as_of`` is the list's own date rather than ours where the list
    publishes one — a check run today against last month's file is only as
    fresh as the file.
    """

    source: str
    outcome: Outcome
    checked_at: datetime
    matches: tuple[ExclusionMatch, ...] = field(default_factory=tuple)
    source_as_of: date | None = None
    #: Why we could not look, when that is the answer. Never shown to a
    #: clinician as-is; it is for the operator and the logs.
    unavailable_reason: str | None = None


def normalise_name(value: str | None) -> str:
    """Upper-case, strip punctuation and drop a trailing suffix.

    Deliberately conservative: it does not transliterate, expand nicknames or
    attempt a fuzzy distance. Every one of those trades a missed exclusion for
    a flood of maybes, and the operator reviewing them is the scarce resource.
    """
    if not value:
        return ""
    joined = _ELIDED.sub("", value.upper())
    collapsed = _TO_SPACE.sub(" ", joined)
    words = [word for word in collapsed.split() if word and word not in _SUFFIXES]
    return " ".join(words)


def _clean(value: str | None) -> str | None:
    """A column's value, or ``None`` where OIG has written a placeholder."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_date(value: str | None) -> date | None:
    """``YYYYMMDD``, or ``None`` for the eight-zero placeholder and anything odd.

    A malformed date is not worth an exception: it costs us one axis of
    disambiguation on one row, and raising would discard the other 84,000.
    """
    cleaned = _clean(value)
    if not cleaned or cleaned == _ABSENT_DATE:
        return None
    try:
        return datetime.strptime(cleaned, "%Y%m%d").date()
    except ValueError:
        return None


def parse_leie(lines: Iterable[str]) -> Iterator[LeieRecord]:
    """Read the OIG CSV into records, one row at a time.

    A generator because the real file is 84,000 rows and a caller matching one
    clinician has no reason to hold them all.

    Rows whose column count does not match the published layout are skipped
    rather than raising, for the reason :func:`_parse_date` gives: one bad row
    must not cost the whole file. A file whose HEADER does not match is a
    different matter — that means the format moved under us, and it raises.
    """
    reader = csv.reader(lines)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ExclusionCheckError("The exclusion file was empty.") from exc

    if tuple(column.strip().upper() for column in header) != LEIE_COLUMNS:
        raise ExclusionCheckError(
            f"The LEIE layout has changed: expected columns {LEIE_COLUMNS}, got {tuple(header)}."
        )

    for row in reader:
        if len(row) != len(LEIE_COLUMNS):
            continue
        values = dict(zip(LEIE_COLUMNS, row, strict=True))
        npi = _clean(values["NPI"])
        yield LeieRecord(
            last_name=_clean(values["LASTNAME"]),
            first_name=_clean(values["FIRSTNAME"]),
            middle_name=_clean(values["MIDNAME"]),
            business_name=_clean(values["BUSNAME"]),
            general=_clean(values["GENERAL"]),
            specialty=_clean(values["SPECIALTY"]),
            npi=None if npi == _ABSENT_NPI else npi,
            date_of_birth=_parse_date(values["DOB"]),
            city=_clean(values["CITY"]),
            state=_clean(values["STATE"]),
            exclusion_type=_clean(values["EXCLTYPE"]),
            excluded_on=_parse_date(values["EXCLDATE"]),
            reinstated_on=_parse_date(values["REINDATE"]),
        )


def _strength(record: LeieRecord, subject: Subject) -> MatchStrength | None:
    """How well one row matches her, or ``None`` if it does not."""
    if subject.npi and record.npi and subject.npi == record.npi:
        return MatchStrength.NPI

    surname = normalise_name(subject.last_name)
    if not surname or normalise_name(record.last_name) != surname:
        return None

    # A surname alone is not a match. Requiring the forename too is what keeps
    # this from returning every Smith on the list.
    given = normalise_name(subject.first_name)
    if not given or normalise_name(record.first_name) != given:
        return None

    if subject.date_of_birth and record.date_of_birth:
        # Same name, different birthday: a different person, and saying so is
        # the single most useful thing a date of birth does here.
        if subject.date_of_birth != record.date_of_birth:
            return None
        return MatchStrength.NAME_AND_DOB

    return MatchStrength.NAME_ONLY


def match_leie(records: Iterable[LeieRecord], subject: Subject) -> list[ExclusionMatch]:
    """Every row that might be her, strongest evidence first.

    Rows carrying a reinstatement date are dropped: the government has said she
    may participate again, and reporting one would contradict it.
    """
    order = {
        MatchStrength.NPI: 0,
        MatchStrength.NAME_AND_DOB: 1,
        MatchStrength.NAME_ONLY: 2,
    }
    matches = [
        ExclusionMatch(record=record, strength=strength)
        for record in records
        if record.is_currently_excluded and (strength := _strength(record, subject)) is not None
    ]
    matches.sort(key=lambda match: order[match.strength])
    return matches


def check_leie(
    records: Iterable[LeieRecord],
    subject: Subject,
    *,
    checked_at: datetime,
    source_as_of: date | None = None,
) -> ExclusionCheck:
    """Match her against an already-loaded LEIE and say what it found."""
    matches = match_leie(records, subject)
    return ExclusionCheck(
        source="leie",
        outcome=Outcome.POSSIBLE_MATCH if matches else Outcome.CLEAR,
        checked_at=checked_at,
        matches=tuple(matches),
        source_as_of=source_as_of,
    )


def download_leie(url: str = LEIE_DOWNLOAD_URL) -> str:
    """Fetch the current LEIE CSV.

    Separate from parsing so that whatever ends up owning the refresh — a job,
    a cache, a table — can hold the bytes without this module having an opinion
    about where they live. It is 15 MB; do not call it inside a request.
    """
    try:
        response = httpx.get(url, timeout=TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ExclusionCheckError(f"Could not fetch the LEIE: {exc}") from exc
    return response.text


def check_sam(
    subject: Subject,
    *,
    checked_at: datetime,
    api_key: str | None,
    base_url: str = SAM_BASE_URL,
) -> ExclusionCheck:
    """Ask SAM.gov about her, or report that we could not.

    **No key is not an error and not a clearance.** A deployment that has not
    been given a SAM key cannot look, and the honest record of that is
    :attr:`Outcome.UNAVAILABLE` with a reason. Returning CLEAR here would be
    the same bug this module exists to fix, one layer down.
    """
    if not api_key:
        return ExclusionCheck(
            source="sam",
            outcome=Outcome.UNAVAILABLE,
            checked_at=checked_at,
            unavailable_reason="No SAM.gov API key is configured for this deployment.",
        )

    params = {
        "api_key": api_key,
        "exclusionName": subject.last_name,
        "isActive": "Y",
    }
    try:
        response = httpx.get(base_url, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return ExclusionCheck(
            source="sam",
            outcome=Outcome.UNAVAILABLE,
            checked_at=checked_at,
            unavailable_reason=f"SAM.gov did not answer: {exc}",
        )

    records = tuple(_sam_records(payload))
    matches = match_leie(records, subject)
    return ExclusionCheck(
        source="sam",
        outcome=Outcome.POSSIBLE_MATCH if matches else Outcome.CLEAR,
        checked_at=checked_at,
        matches=tuple(matches),
    )


def _sam_records(payload: object) -> Iterator[LeieRecord]:
    """SAM's exclusion entries, reshaped into the record type used above.

    One type for two lists so the operator's review screen does not need to
    know which one raised the question. SAM carries no date of birth, so
    everything it produces can only ever reach ``NAME_ONLY`` — which is true,
    and better said in the data than in a comment on the screen.
    """
    if not isinstance(payload, dict):
        return
    entries = payload.get("excludedEntity") or payload.get("excludedEntities") or []
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("exclusionName") or {}
        individual = name if isinstance(name, dict) else {}
        yield LeieRecord(
            last_name=individual.get("lastName") or _sam_surname(entry),
            first_name=individual.get("firstName"),
            middle_name=individual.get("middleName"),
            business_name=entry.get("exclusionName") if isinstance(name, str) else None,
            general=entry.get("exclusionType"),
            specialty=entry.get("exclusionProgram"),
            npi=None,
            date_of_birth=None,
            city=None,
            state=None,
            exclusion_type=entry.get("exclusionType"),
            excluded_on=None,
            reinstated_on=None,
        )


def _sam_surname(entry: dict[str, object]) -> str | None:
    """The surname out of SAM's flat name string, which is ``LAST, FIRST``."""
    raw = entry.get("exclusionName")
    if not isinstance(raw, str) or not raw:
        return None
    return raw.split(",", 1)[0].strip() or None
