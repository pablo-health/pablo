# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Checking a claim before it leaves the practice.

A claim goes out once and is either accepted or bounced; every bounce is a
week lost and a person re-keying something. So the checks a clearinghouse
and a payer will make are made here first, on the draft, and the claim
cannot advance to ``validated`` while any *blocking* finding stands. A
*warning* is something worth a look that does not stop the claim.

Every rule is a small function with a docstring saying what it catches and
why; :func:`scrub` runs them in a fixed order and concatenates what they
return. Rules are code, not a configuration table, on purpose: they are
few, each has a test in both directions, and reading the list is the
documentation. Nothing here consults a model, a network or the clock — the
one date-relative rule takes ``today`` as an argument, so the same claim on
the same day yields the same findings in the same order.

Several rules are keyed to edits the clearinghouse has actually returned
for a claim from this codebase (the recorded fixtures under
``tests/fixtures/clearinghouse/837p_submission_edit_rejected_*.json``):
diagnosis pointers that name no diagnosis, a category-level ICD-10 code,
and a self-subscriber with no demographics. Those rules exist so the claim
never leaves ``draft`` in a shape the clearinghouse has been seen to refuse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal

from ..db.models import CLAIM_CONTROL_NUMBER_MAX_LENGTH
from .validation import dx_at_highest_specificity, dx_pointers_valid, missing_fields

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ..models.claims import Claim, ClaimLine, PersonSnapshot

Severity = Literal["blocking", "warning"]


@dataclass(frozen=True)
class Finding:
    """One thing wrong (``blocking``) or worth a look (``warning``) on a claim.

    ``code`` is stable and machine-readable so a screen can group or filter
    on it; ``message`` is for the person fixing the claim; ``field`` names
    where on the claim the problem is, when there is one place.
    """

    severity: Severity
    code: str
    message: str
    field: str | None = None


#: Psychotherapy add-on codes: never billed alone, always alongside a base
#: service on the same date — 90833/90836/90838 ride on an evaluation-and-
#: management visit, 90840 on a 90839 crisis session.
ADD_ON_CODES: frozenset[str] = frozenset({"90833", "90836", "90838", "90840"})

#: Place-of-service codes for a visit held over video: 02 (telehealth,
#: not the client's home) and 10 (telehealth in the client's home).
TELEHEALTH_POS: frozenset[str] = frozenset({"02", "10"})

#: The modifier that marks a synchronous telehealth service.
TELEHEALTH_MODIFIER = "95"

#: An 837P service line carries at most four modifiers (SV101-3..6).
MAX_MODIFIERS_PER_LINE = 4

#: X12 basic character set, which a control number must stay inside:
#: upper-case letters, digits and a few marks. No lower case, no delimiter.
_X12_BASIC = re.compile(r"^[A-Z0-9 !\"&'()+,\-./;?=]+$")

#: Characters the X12 envelope uses as delimiters. One of these inside a
#: name or an address splits the segment and the whole file is refused.
_X12_DELIMITERS = frozenset("~*:^>")

#: Older than this and the date of birth is a typo, not a person.
_MAX_AGE_YEARS = 120

#: A US phone number on the wire is exactly ten digits.
_PHONE_DIGITS = 10

_BILLING_PROVIDER_REQUIRED = [
    "legal_name",
    "npi",
    "address_line1",
    "city",
    "state",
    "postal_code",
    "phone",
    "tax_id_last4",
    "tax_id_type",
]
_RENDERING_PROVIDER_REQUIRED = ["first_name", "last_name", "npi"]
_PERSON_REQUIRED = ["first_name", "last_name"]
_DEMOGRAPHICS_REQUIRED = [
    "date_of_birth",
    "sex",
    "address_line1",
    "city",
    "state",
    "postal_code",
]


def _blocking(code: str, message: str, field: str | None = None) -> Finding:
    return Finding("blocking", code, message, field)


def _warning(code: str, message: str, field: str | None = None) -> Finding:
    return Finding("warning", code, message, field)


def _missing(obj: object, required: list[str], prefix: str) -> Iterator[Finding]:
    for name in missing_fields(obj, required):
        yield _blocking("missing_field", f"{prefix}.{name} is required.", f"{prefix}.{name}")


# ---------------------------------------------------------------------------
# Rules. Each takes the claim and returns what it found; the order they run
# in is the order of ``_RULES`` below.
# ---------------------------------------------------------------------------


def required_fields(claim: Claim) -> list[Finding]:
    """Everything the claim header, the providers and the people must carry.

    A claim with a blank in any of these is refused before it is even
    read for content. Uses the shared ``missing_fields`` helper so this
    list and the superbill's agree on what "missing" means.
    """
    findings: list[Finding] = []
    billing = claim.billing_snapshot
    subscriber = claim.subscriber_snapshot
    findings.extend(
        _missing(billing.billing_provider, _BILLING_PROVIDER_REQUIRED, "billing_provider")
    )
    findings.extend(
        _missing(billing.rendering_provider, _RENDERING_PROVIDER_REQUIRED, "rendering_provider")
    )
    findings.extend(_missing(subscriber, ["member_id"], "subscriber"))
    findings.extend(_missing(subscriber.subscriber, _PERSON_REQUIRED, "subscriber"))
    findings.extend(_missing(subscriber.patient, _PERSON_REQUIRED, "patient"))
    if not claim.place_of_service:
        findings.append(
            _blocking("missing_field", "place_of_service is required.", "place_of_service")
        )
    if not claim.diagnosis_codes:
        findings.append(
            _blocking(
                "missing_field",
                "At least one diagnosis code is required.",
                "diagnosis_codes",
            )
        )
    if not claim.lines:
        findings.append(_blocking("missing_field", "A claim needs at least one line.", "lines"))
    for line in claim.lines:
        if not line.cpt or not line.cpt.strip():
            findings.append(
                _blocking(
                    "missing_field",
                    f"Line {line.line_number} has no service code.",
                    _lf(line, "cpt"),
                )
            )
    return findings


def payer_id_known(claim: Claim) -> list[Finding]:
    """A payer typed from the card with no electronic id cannot be filed to.

    The coverage form stores ``UNKNOWN`` when a client's card shows no
    payer id; the practice fixes it up from the payer directory later.
    Until then there is nowhere to send the claim.
    """
    if claim.subscriber_snapshot.payer_id.strip().upper() == "UNKNOWN":
        return [
            _blocking(
                "payer_unknown",
                "The payer has no electronic payer id. Look it up in the payer directory "
                "and update the payer before filing.",
                "payer",
            )
        ]
    return []


def coverage_active(claim: Claim) -> list[Finding]:
    """The plan the claim is built on must still be on file and active."""
    if not claim.subscriber_snapshot.coverage_active:
        return [
            _blocking(
                "coverage_inactive",
                "The coverage this claim was built from is no longer active.",
                "coverage",
            )
        ]
    return []


def place_of_service_matches_visit(claim: Claim) -> list[Finding]:
    """A visit held over video cannot be billed as an office visit.

    Place of service 11 (office) on a telehealth appointment is the most
    common self-inflicted denial in outpatient behavioral health. The fix
    is 10 (telehealth, client at home) or 02 (telehealth elsewhere), with
    modifier 95 on the line — suggested here, never applied silently.
    """
    if claim.place_of_service != "11":
        return []
    return [
        _blocking(
            "pos_telehealth_mismatch",
            f"Line {line.line_number} was a video visit but the place of service is 11 "
            "(office). Use 10 (client at home) or 02 (elsewhere) with modifier 95.",
            "place_of_service",
        )
        for line in claim.lines
        if line.telehealth
    ]


def telehealth_modifier_present(claim: Claim) -> list[Finding]:
    """A telehealth place of service usually wants modifier 95 on each line.

    Not every payer requires it, so this is a warning: the claim goes out
    without it if the practice says so.
    """
    if claim.place_of_service not in TELEHEALTH_POS:
        return []
    return [
        _warning(
            "telehealth_modifier_missing",
            f"Line {line.line_number} is billed at a telehealth place of service "
            f"without modifier {TELEHEALTH_MODIFIER}.",
            _lf(line, "modifiers"),
        )
        for line in claim.lines
        if TELEHEALTH_MODIFIER not in line.modifiers
    ]


def add_on_has_base_service(claim: Claim) -> list[Finding]:
    """A psychotherapy add-on code needs a base service on the same date.

    90833/90836/90838/90840 are add-ons by definition; a line carrying one
    with no other, non-add-on line on the same service date is refused by
    every payer.
    """
    findings: list[Finding] = []
    for line in claim.lines:
        if line.cpt not in ADD_ON_CODES:
            continue
        has_base = any(
            other.cpt not in ADD_ON_CODES and other.service_date == line.service_date
            for other in claim.lines
            if other is not line
        )
        if not has_base:
            findings.append(
                _blocking(
                    "add_on_without_base",
                    f"Line {line.line_number} ({line.cpt}) is an add-on code with no base "
                    "service on the same date.",
                    _lf(line, "cpt"),
                )
            )
    return findings


def modifiers_within_limit(claim: Claim) -> list[Finding]:
    """A service line carries at most four modifiers on the wire."""
    return [
        _blocking(
            "too_many_modifiers",
            f"Line {line.line_number} carries {len(line.modifiers)} modifiers; "
            f"the limit is {MAX_MODIFIERS_PER_LINE}.",
            _lf(line, "modifiers"),
        )
        for line in claim.lines
        if len(line.modifiers) > MAX_MODIFIERS_PER_LINE
    ]


def diagnosis_pointers_valid(claim: Claim) -> list[Finding]:
    """Every line's diagnosis pointers must name a diagnosis on the claim.

    Keyed to the clearinghouse edit recorded in
    ``837p_submission_edit_rejected_dx_pointer.json`` ("The Diagnosis
    Pointer(s) of 2 on line 1 is/are invalid"): a pointer past the end of
    the diagnosis list, a line with none, more than four, or a repeat.
    """
    n_dx = len(claim.diagnosis_codes)
    return [
        _blocking(
            "dx_pointer_invalid",
            f"Line {line.line_number} points at diagnosis positions {line.dx_pointers}, "
            f"but the claim carries {n_dx} diagnosis code(s). Each line needs one to four "
            "distinct pointers into that list.",
            _lf(line, "dx_pointers"),
        )
        for line in claim.lines
        if not dx_pointers_valid(line.dx_pointers, n_dx)
    ]


def diagnosis_codes_specific(claim: Claim) -> list[Finding]:
    """Every diagnosis must be an ICD-10-CM code carried past its category.

    Keyed to the clearinghouse edit recorded in
    ``837p_submission_edit_rejected_dx_specificity.json`` ("Diagnosis code
    F41 is category codes and considered non-billable"): F41 is refused,
    F41.1 is accepted.
    """
    return [
        _blocking(
            "dx_not_specific",
            f"Diagnosis {position} ({code}) is not a billable ICD-10-CM code. "
            "Use the code at its highest level of specificity (F41.1, not F41).",
            f"diagnosis_codes[{position - 1}]",
        )
        for position, code in enumerate(claim.diagnosis_codes, start=1)
        if not dx_at_highest_specificity(code)
    ]


def subscriber_demographics_present(claim: Claim) -> list[Finding]:
    """Whoever the subscriber is, their date of birth, sex and address go on the claim.

    Keyed to the clearinghouse edit recorded in
    ``837p_submission_edit_rejected_subscriber_demographics.json``
    ("When the patient is the subscriber, the subscriber address and
    demographics are required"). When the subscriber is somebody other
    than the client, the same fields are required of that person — and the
    client's own date of birth and sex still go in the patient loop.
    """
    snapshot = claim.subscriber_snapshot
    findings = [
        _blocking(
            "subscriber_demographics_missing",
            f"subscriber.{name} is required.",
            f"subscriber.{name}",
        )
        for name in missing_fields(snapshot.subscriber, _DEMOGRAPHICS_REQUIRED)
    ]
    if snapshot.relationship != "self":
        findings.extend(
            _blocking(
                "subscriber_demographics_missing",
                f"patient.{name} is required when the client is not the subscriber.",
                f"patient.{name}",
            )
            for name in missing_fields(snapshot.patient, ["date_of_birth", "sex"])
        )
    return findings


def dates_of_birth_plausible(claim: Claim, *, today: date) -> list[Finding]:
    """A date of birth in the future, or more than 120 years back, is a typo."""
    snapshot = claim.subscriber_snapshot
    findings: list[Finding] = []
    for prefix, person in (("subscriber", snapshot.subscriber), ("patient", snapshot.patient)):
        finding = _dob_finding(person, prefix, today)
        if finding is not None:
            findings.append(finding)
    return findings


def _dob_finding(person: PersonSnapshot, prefix: str, today: date) -> Finding | None:
    dob = person.date_of_birth
    if dob is None:
        return None
    if dob > today:
        return _blocking(
            "dob_implausible",
            f"{prefix}.date_of_birth is in the future.",
            f"{prefix}.date_of_birth",
        )
    oldest = date(today.year - _MAX_AGE_YEARS, today.month, min(today.day, 28))
    if dob < oldest:
        return _blocking(
            "dob_implausible",
            f"{prefix}.date_of_birth is more than {_MAX_AGE_YEARS} years ago.",
            f"{prefix}.date_of_birth",
        )
    return None


def charges_positive(claim: Claim) -> list[Finding]:
    """A line billed for nothing is a line with no rate on file, not a free visit.

    The rate resolves from the client's override or the appointment type;
    when neither is set the assembly writes 0 rather than guessing, and the
    claim stops here.
    """
    return [
        _blocking(
            "charge_zero",
            f"Line {line.line_number} has no charge. Set a rate on the client or the "
            "appointment type and rebuild the claim.",
            _lf(line, "charge_cents"),
        )
        for line in claim.lines
        if line.charge_cents <= 0
    ]


def units_positive(claim: Claim) -> list[Finding]:
    """Every line bills at least one unit."""
    return [
        _blocking(
            "units_invalid",
            f"Line {line.line_number} bills {line.units} units.",
            _lf(line, "units"),
        )
        for line in claim.lines
        if line.units < 1
    ]


def total_matches_lines(claim: Claim) -> list[Finding]:
    """The claim's charge (CLM02) must equal the sum of its lines."""
    expected = sum(line.charge_cents for line in claim.lines)
    if claim.total_charge_cents == expected:
        return []
    return [
        _blocking(
            "total_mismatch",
            f"The claim total is {claim.total_charge_cents} cents but the lines add up "
            f"to {expected}.",
            "total_charge_cents",
        )
    ]


def control_numbers_well_formed(claim: Claim) -> list[Finding]:
    """CLM01 and each line's REF*6R must fit the wire.

    At most 17 characters for the claim's own number, inside the X12 basic
    character set, and distinct per line — the clearinghouse matches its
    acknowledgements back on these, so a malformed one is refused outright.
    """
    findings: list[Finding] = []
    control = claim.control_number
    if (
        not control
        or len(control) > CLAIM_CONTROL_NUMBER_MAX_LENGTH
        or not _X12_BASIC.match(control)
    ):
        findings.append(
            _blocking(
                "control_number_invalid",
                f"The claim control number must be 1-{CLAIM_CONTROL_NUMBER_MAX_LENGTH} "
                "characters from the X12 basic character set.",
                "control_number",
            )
        )
    seen: set[str] = set()
    for line in claim.lines:
        number = line.line_control_number
        if not number or not _X12_BASIC.match(number) or number in seen:
            findings.append(
                _blocking(
                    "control_number_invalid",
                    f"Line {line.line_number} has a missing, malformed or duplicate "
                    "line control number.",
                    _lf(line, "line_control_number"),
                )
            )
        seen.add(number)
    return findings


def no_delimiter_characters(claim: Claim) -> list[Finding]:
    """No name, address or plan field may contain an X12 delimiter.

    ``~ * : ^ >`` split segments and elements on the wire; one inside a
    value corrupts the whole interchange. Checked on every text value the
    claim carries, so the finding names the field to fix.
    """
    return [
        _blocking(
            "x12_delimiter_in_text",
            f"{path} contains a character the claim format reserves (one of ~ * : ^ >).",
            path,
        )
        for path, value in _text_values(claim)
        if _X12_DELIMITERS & set(value)
    ]


def phone_numbers_ten_digits(claim: Claim) -> list[Finding]:
    """The billing provider's phone must be exactly ten digits on the wire.

    The number is sent as digits only; anything else — an extension, a
    country code, a number that is too short — is refused.
    """
    phone = claim.billing_snapshot.billing_provider.phone
    if phone is None:
        return []
    digits = re.sub(r"\D", "", phone)
    if len(digits) == _PHONE_DIGITS:
        return []
    return [
        _blocking(
            "phone_not_ten_digits",
            "billing_provider.phone must contain exactly ten digits.",
            "billing_provider.phone",
        )
    ]


def rendering_taxonomy_present(claim: Claim) -> list[Finding]:
    """A rendering provider with no taxonomy code is a warning, not a stop.

    Most payers accept the claim without it; some deny. It lives on the
    clinician's profile and takes a minute to add.
    """
    if claim.billing_snapshot.rendering_provider.taxonomy_code:
        return []
    return [
        _warning(
            "taxonomy_missing",
            "The rendering provider has no taxonomy code on their profile. Some payers "
            "deny without one.",
            "rendering_provider.taxonomy_code",
        )
    ]


# ---------------------------------------------------------------------------
# Running them
# ---------------------------------------------------------------------------


def _lf(line: ClaimLine, name: str) -> str:
    return f"lines[{line.line_number - 1}].{name}"


def _text_values(claim: Claim) -> Iterator[tuple[str, str]]:
    """Every free-text value on the claim, with a dotted path naming it."""
    billing = claim.billing_snapshot
    subscriber = claim.subscriber_snapshot
    sections: list[tuple[str, dict[str, object]]] = [
        ("billing_provider", billing.billing_provider.model_dump()),
        ("rendering_provider", billing.rendering_provider.model_dump()),
        ("subscriber", subscriber.subscriber.model_dump()),
        ("patient", subscriber.patient.model_dump()),
        (
            "subscriber",
            {
                "member_id": subscriber.member_id,
                "group_number": subscriber.group_number,
                "plan_name": subscriber.plan_name,
                "payer_name": subscriber.payer_name,
            },
        ),
    ]
    for prefix, values in sections:
        for name, value in values.items():
            if isinstance(value, str):
                yield f"{prefix}.{name}", value


_RULES: tuple[Callable[[Claim], list[Finding]], ...] = (
    required_fields,
    payer_id_known,
    coverage_active,
    place_of_service_matches_visit,
    telehealth_modifier_present,
    add_on_has_base_service,
    modifiers_within_limit,
    diagnosis_pointers_valid,
    diagnosis_codes_specific,
    subscriber_demographics_present,
    charges_positive,
    units_positive,
    total_matches_lines,
    control_numbers_well_formed,
    no_delimiter_characters,
    phone_numbers_ten_digits,
    rendering_taxonomy_present,
)


def scrub(claim: Claim, *, today: date | None = None) -> list[Finding]:
    """Every finding on ``claim``, blocking ones and warnings alike, in rule order.

    Deterministic: the same claim on the same ``today`` yields the same list
    in the same order. ``today`` defaults to the calendar date and only
    feeds the date-of-birth plausibility rule.
    """
    reference = today if today is not None else date.today()
    findings: list[Finding] = []
    for rule in _RULES:
        findings.extend(rule(claim))
        if rule is subscriber_demographics_present:
            findings.extend(dates_of_birth_plausible(claim, today=reference))
    return findings


def blocking(findings: list[Finding]) -> list[Finding]:
    """Only the findings that stop the claim."""
    return [finding for finding in findings if finding.severity == "blocking"]
