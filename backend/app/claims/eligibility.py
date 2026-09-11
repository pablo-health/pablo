# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Checking a client's plan before the first session: a 270 out, a 271 back.

The check runs through the practice's own clearinghouse account. It asks
about outpatient mental health (service type ``MH``) for the client whose
coverage is on file, stores the whole parsed 271 on the coverage row, and
reads it down to :class:`~app.models.eligibility.EligibilitySummary`: is the
plan active, what the client pays at the door, how much deductible is left,
whether visits are capped or need an authorization, and whether somebody
other than the payer on the card administers behavioral benefits.

That last one is the trap specific to this specialty. A medical card names
one payer; the behavioral benefit is often carved out to another
administrator, and a claim filed with the payer on the card is denied for
"not our benefit". Where a 271 says so, and how this module finds it:

* ``benefitsInformation[].code == "U"`` ("Contact Following Entity for
  Eligibility or Benefit Information") whose ``serviceTypeCodes`` name a
  behavioral benefit. The entity is in ``benefitsRelatedEntities[]`` (the
  vendor also mirrors the first one as ``benefitsRelatedEntity``), with
  ``entityName`` and, when the payer sends an id, ``entityIdentification
  == "PI"`` + ``entityIdentificationValue`` as the administrator's payer id.
  The vendor's mock UnitedHealthcare member answers exactly this shape for
  its pharmacy carve-out (service type ``88``, entity "OPTUMRX"); the
  behavioral fixture is that line with the service type and entity swapped
  (``eligibility_271_carveout_behavioral.json``, constructed).
* An active behavioral line (``code`` 1-5 with a behavioral service type)
  whose related entity is a ``Payer`` or ``Third-Party Administrator`` with
  a payer id other than the responding payer's own ``payorIdentification``.
  The mock's plan-level line carries the responding payer itself here, so a
  different id on a behavioral line is the second signal worth reading.

Nothing here is a payment guarantee, and the copy that renders the summary
says so: "plan active as of", never "covered".

Logging: the payer id, the trigger, the coverage row id and the outcome
status. Never the member id, never the subscriber, never the 271.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Literal

from ..db.models import ClinicianProfileRow
from ..models.audit import ACTOR_TYPE_CLINICIAN, ACTOR_TYPE_SYSTEM
from ..models.claims_transport import (
    EligibilityBenefit,
    EligibilityDependent,
    EligibilityEncounter,
    EligibilityProvider,
    EligibilityRelatedEntity,
    EligibilityRequest,
    EligibilityResponse,
    EligibilitySubscriber,
)
from ..models.eligibility import (
    AaaError,
    CarveoutAdministrator,
    EligibilityOutcome,
    EligibilityStatus,
    EligibilitySummary,
    EligibilityTrigger,
    VisitLimit,
)
from ..money import dollars_to_cents
from ..services.coverage_intake import UNKNOWN_PAYER_ID
from ..services.practice_billing_profile import load_billing_profile
from ..utcnow import utc_now
from .clearinghouse import ClearinghouseError
from .events import ClaimEvent, ClaimEventDetail, CodeRef, emit

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from sqlalchemy.orm import Session

    from ..models import Patient, User
    from ..models.coverage import PatientCoverage, Payer
    from ..repositories.coverage import PatientCoverageRepository, PayerRepository
    from ..repositories.patient import PatientRepository
    from .clearinghouse import ClearinghouseClient

logger = logging.getLogger(__name__)

#: What the 270 asks about. ``MH`` (Mental Health) only: the benefit an
#: outpatient psychotherapy visit is billed under. The X12 codes for the
#: same family (``A4`` psychiatric, ``A6`` psychotherapy, ``A8`` psychiatric
#: outpatient, ``CG``/``CI`` mental health provider/facility outpatient)
#: are read on the way back, since a payer answers in its own vocabulary.
REQUESTED_SERVICE_TYPE_CODES: tuple[str, ...] = ("MH",)

BEHAVIORAL_SERVICE_TYPE_CODES: frozenset[str] = frozenset({"MH", "A4", "A6", "A8", "CG", "CI"})

#: ``30`` is "Health Benefit Plan Coverage": the plan as a whole.
_PLAN_LEVEL_SERVICE_TYPE_CODE = "30"

_ACTIVE_CODES = frozenset({"1", "2", "3", "4", "5"})
_INACTIVE_CODES = frozenset({"6", "7", "8"})
_COINSURANCE = "A"
_COPAYMENT = "B"
_DEDUCTIBLE = "C"
_LIMITATIONS = "F"
_CONTACT_ENTITY = "U"

#: EB06 time period qualifiers.
_TIME_REMAINING = "29"
_TIME_PER_VISIT = "27"

#: EB09 quantity qualifier for a visit count.
_QUANTITY_VISITS = "VS"

_IN_NETWORK = "Y"
_INDIVIDUAL = "IND"
_PAYER_ID_QUALIFIER = "PI"
_ADMINISTRATOR_ROLES = frozenset({"Payer", "Third-Party Administrator"})

#: The path the queued check is delivered back to; see ``check_eligibility_job``.
ELIGIBILITY_JOB_PATH = "/api/internal/jobs/check-eligibility"


class EligibilityNotPossibleError(Exception):
    """The check cannot be built for this coverage: nothing was sent.

    Not a vendor failure and not transient — the practice has no
    clearinghouse credentials, no NPI on file, or the payer on the card has
    no electronic id yet. The message is written for the person who can fix
    it and is safe to show.
    """


class CoverageNotFoundError(Exception):
    """The coverage row is gone, or its client is not visible to this user."""


# ---------------------------------------------------------------------------
# Reading a 271
# ---------------------------------------------------------------------------


def _is_behavioral(codes: Iterable[str]) -> bool:
    return not BEHAVIORAL_SERVICE_TYPE_CODES.isdisjoint(codes)


def _is_plan_level(codes: list[str]) -> bool:
    return not codes or _PLAN_LEVEL_SERVICE_TYPE_CODE in codes


def _status(response: EligibilityResponse) -> EligibilityStatus:
    """Behavioral lines answer first; the plan as a whole is the fallback.

    A plan can be active while its behavioral benefit is not (a rider that
    lapsed, a carve-out the payer no longer administers), so an explicit
    behavioral answer outranks the plan-level one in either direction.
    """
    lines: list[tuple[str, list[str]]] = [
        (b.code, b.serviceTypeCodes) for b in response.benefitsInformation
    ] + [(p.statusCode, p.serviceTypeCodes) for p in response.planStatus]

    def any_with(codes: frozenset[str], scope: Callable[[list[str]], bool]) -> bool:
        return any(code in codes and scope(stcs) for code, stcs in lines)

    if any_with(_ACTIVE_CODES, _is_behavioral):
        return "active"
    if any_with(_INACTIVE_CODES, _is_behavioral):
        return "inactive"
    if any_with(_ACTIVE_CODES, _is_plan_level):
        return "active"
    if any_with(_INACTIVE_CODES, _is_plan_level):
        return "inactive"
    return "unknown"


def _applies_here(line: EligibilityBenefit) -> bool:
    """A line about the behavioral benefit, or about the plan as a whole.

    A copay printed for physical therapy or a specialist visit says nothing
    about a psychotherapy visit, so a line scoped to some other service type
    is never read — better an empty field than the wrong number.
    """
    return _is_behavioral(line.serviceTypeCodes) or _is_plan_level(line.serviceTypeCodes)


def _rank(line: EligibilityBenefit) -> tuple[int, int, int]:
    """Lower is better: the behavioral, in-network, individual line first."""
    return (
        0 if _is_behavioral(line.serviceTypeCodes) else 1,
        0 if line.inPlanNetworkIndicatorCode in (_IN_NETWORK, None, "W") else 1,
        0 if line.coverageLevelCode in (_INDIVIDUAL, None) else 1,
    )


def _pick(
    response: EligibilityResponse, code: str, *, time_qualifier: str | None = None
) -> EligibilityBenefit | None:
    candidates = [
        b
        for b in response.benefitsInformation
        if b.code == code
        and _applies_here(b)
        and (time_qualifier is None or b.timeQualifierCode == time_qualifier)
    ]
    return min(candidates, key=_rank) if candidates else None


def _cents(amount: str | None) -> int | None:
    if amount is None:
        return None
    try:
        return dollars_to_cents(amount)
    except ValueError:
        return None


def _percent(fraction: str | None) -> float | None:
    """``"0.20"`` on the wire is the client's 20% share."""
    if fraction is None:
        return None
    try:
        return float(Decimal(fraction) * 100)
    except (InvalidOperation, ValueError):
        return None


def _quantity(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(Decimal(value))
    except (InvalidOperation, ValueError):
        return None


def _visit_limit(response: EligibilityResponse) -> VisitLimit | None:
    remaining: int | None = None
    total: int | None = None
    for line in sorted(response.benefitsInformation, key=_rank):
        if line.code != _LIMITATIONS or line.quantityQualifierCode != _QUANTITY_VISITS:
            continue
        if not _applies_here(line):
            continue
        count = _quantity(line.benefitQuantity)
        if count is None:
            continue
        if line.timeQualifierCode == _TIME_REMAINING:
            remaining = remaining if remaining is not None else count
        else:
            total = total if total is not None else count
    if remaining is None and total is None:
        return None
    return VisitLimit(remaining=remaining, total=total)


def _requires_authorization(response: EligibilityResponse) -> bool | None:
    answers = [
        b.authOrCertIndicator
        for b in response.benefitsInformation
        if _is_behavioral(b.serviceTypeCodes) and b.authOrCertIndicator in ("Y", "N")
    ]
    if not answers:
        return None
    return "Y" in answers


def _entities(line: EligibilityBenefit) -> list[EligibilityRelatedEntity]:
    if line.benefitsRelatedEntities:
        return line.benefitsRelatedEntities
    return [line.benefitsRelatedEntity] if line.benefitsRelatedEntity is not None else []


def _administrator(entity: EligibilityRelatedEntity) -> CarveoutAdministrator | None:
    if not entity.entityName:
        return None
    payer_id = (
        entity.entityIdentificationValue
        if entity.entityIdentification == _PAYER_ID_QUALIFIER
        else None
    )
    return CarveoutAdministrator(name=entity.entityName, payer_id=payer_id)


def _carveout_administrator(response: EligibilityResponse) -> CarveoutAdministrator | None:
    """See the module docstring for the two 271 shapes this reads."""
    own_id = response.payer.payorIdentification if response.payer else None
    own_name = (response.payer.name or "").strip().lower() if response.payer else ""

    for line in response.benefitsInformation:
        if line.code != _CONTACT_ENTITY or not _is_behavioral(line.serviceTypeCodes):
            continue
        for entity in _entities(line):
            found = _administrator(entity)
            if found is not None:
                return found

    for line in response.benefitsInformation:
        if line.code not in _ACTIVE_CODES or not _is_behavioral(line.serviceTypeCodes):
            continue
        for entity in _entities(line):
            if entity.entityIdentifier not in _ADMINISTRATOR_ROLES:
                continue
            found = _administrator(entity)
            if found is None:
                continue
            if found.payer_id is not None:
                if own_id is not None and found.payer_id != own_id:
                    return found
            elif found.name.strip().lower() != own_name:
                return found
    return None


def _plan_name(response: EligibilityResponse) -> str | None:
    for status in response.planStatus:
        if status.planDetails:
            return status.planDetails
    for line in response.benefitsInformation:
        if line.code in _ACTIVE_CODES and line.planCoverage:
            return line.planCoverage
    return None


def _iso_date(yyyymmdd: str | None) -> str | None:
    if yyyymmdd is None or len(yyyymmdd) != 8 or not yyyymmdd.isdigit():  # noqa: PLR2004 — YYYYMMDD
        return None
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def outcome_from_271(response: EligibilityResponse) -> EligibilityOutcome:
    """Read a 271 down to the chart's answer. Pure: no I/O, no logging.

    The same outcome ``app.claims.sdk_eligibility`` produces from the vendor's
    own eligibility API, so that a caller cannot tell which client answered.
    The difference is where the reading happens: here the benefit lines carry
    X12 ``EB01`` codes and this module sorts them out; there they arrive
    already sorted by kind.
    """
    payer_name = response.payer.name if response.payer else None
    stored = response.model_dump(mode="json", exclude_none=True)
    if response.errors:
        return EligibilityOutcome(
            status="error",
            payer_name=payer_name,
            aaa_errors=[
                AaaError(
                    code=e.code,
                    description=e.description,
                    followup_action=e.followupAction,
                    resolution=e.possibleResolutions,
                )
                for e in response.errors
            ],
            stored=stored,
        )

    copay = _pick(response, _COPAYMENT, time_qualifier=_TIME_PER_VISIT) or _pick(
        response, _COPAYMENT
    )
    coinsurance = _pick(response, _COINSURANCE)
    deductible = _pick(response, _DEDUCTIBLE, time_qualifier=_TIME_REMAINING)
    plan_dates = response.planDateInformation
    return EligibilityOutcome(
        status=_status(response),
        payer_name=payer_name,
        plan_name=_plan_name(response),
        plan_begin=_iso_date(plan_dates.planBegin) if plan_dates else None,
        copay_cents=_cents(copay.benefitAmount) if copay else None,
        coinsurance_pct=_percent(coinsurance.benefitPercent) if coinsurance else None,
        deductible_remaining_cents=_cents(deductible.benefitAmount) if deductible else None,
        visit_limit=_visit_limit(response),
        requires_authorization=_requires_authorization(response),
        carveout_administrator=_carveout_administrator(response),
        stored=stored,
    )


def summarize_271(response: EligibilityResponse, *, checked_at: datetime) -> EligibilitySummary:
    """That outcome, as of when it was asked."""
    return outcome_from_271(response).at(checked_at)


def summary_for_coverage(coverage: PatientCoverage) -> EligibilitySummary | None:
    """The stored 271 on a coverage row, read down; ``None`` before any check."""
    if coverage.last_271 is None or coverage.verified_at is None:
        return None
    response = EligibilityResponse.model_validate(coverage.last_271)
    return summarize_271(response, checked_at=coverage.verified_at)


# ---------------------------------------------------------------------------
# What an AAA rejection means
# ---------------------------------------------------------------------------

#: What to do about an AAA rejection: retry the same request, fix something
#: about it first, or stop for a person (or an enrollment) because no retry
#: changes the answer.
RejectDisposition = Literal["retry_as_is", "fix_then_retry", "stop_enrollment", "stop_manual"]

HTTP_TOO_MANY_REQUESTS = 429

#: 42 "unable to respond at current time", 80 "no response received /
#: transaction terminated" — the payer or the gateway in front of it is
#: down. Nothing about the request is wrong, so the same 270 is worth
#: sending again once it comes back.
_RETRY_AS_IS_CODES = frozenset({"42", "80"})

#: 79 "invalid or missing entity identification number" doubles as "unknown
#: or unconfigured payer id" when it arrives alone. Riding alongside 42 it
#: is the gateway's own way of saying "try again" — see _RETRY_AS_IS_CODES,
#: checked first — but alone it means the payer id on file is wrong, and no
#: amount of retrying fixes a payer id.
_UNCONFIGURED_PAYER_CODE = "79"

#: 75 subscriber not found, 72 invalid/missing member id, 73 name mismatch,
#: 65/67 date-of-birth and other required-field format problems, 56/57/58
#: missing required fields — every one of these is a request that a
#: different member id, name, or date shape could get past.
_FIX_THEN_RETRY_CODES = frozenset({"75", "72", "73", "65", "67", "56", "57", "58"})

#: 41 provider not eligible, 43 / 51 provider not on file with this payer —
#: the practice itself has no enrollment with the payer being asked. No
#: retry, and no fix to the request, changes that; only an enrollment does.
_ENROLLMENT_CODES = frozenset({"41", "43", "51"})


def reject_disposition(
    codes: Iterable[str], *, http_status: int | None = None
) -> RejectDisposition:
    """What to do about the AAA codes (and/or HTTP status) a check came back with.

    Order matters: an enrollment gap always wins — retrying or fixing the
    request is pointless when the practice isn't enrolled with the payer at
    all. Next the transport-is-down codes, an HTTP 429 counted the same way
    since a rate limit means the same thing. Then 79 riding alone: an
    unconfigured payer id nobody can retry into working. Then everything a
    changed request could clear. A code this table has never seen stops
    rather than guessing.
    """
    code_set = set(codes)
    if code_set & _ENROLLMENT_CODES:
        return "stop_enrollment"
    if http_status == HTTP_TOO_MANY_REQUESTS or code_set & _RETRY_AS_IS_CODES:
        return "retry_as_is"
    if _UNCONFIGURED_PAYER_CODE in code_set:
        return "stop_manual"
    if code_set & _FIX_THEN_RETRY_CODES:
        return "fix_then_retry"
    return "stop_manual"


#: Real-time retry-as-is budget: keep retrying immediately while a
#: clinician is waiting on the button, then give up rather than hang.
REALTIME_RETRY_BUDGET_SECONDS = 120.0

#: Scheduled retry-as-is backoff: doubling from one minute, capped at
#: thirty — a payer outage that takes an hour to clear should not be
#: hammered every sixty seconds in the meantime.
SCHEDULED_RETRY_INITIAL_SECONDS = 60.0
SCHEDULED_RETRY_MAX_SECONDS = 1800.0


def next_retry_delay_seconds(
    *, attempt: int, elapsed_seconds: float, realtime: bool
) -> float | None:
    """Seconds before the next ``retry_as_is`` attempt, or ``None`` to stop.

    ``attempt`` counts attempts already made (1 after the first failure).
    A real-time check retries immediately until ``elapsed_seconds`` passes
    the budget, then gives up. A scheduled check backs off exponentially
    with no fixed end here — the queue's own attempt limit is what
    eventually stops it, this only shapes the spacing.
    """
    if realtime:
        return 0.0 if elapsed_seconds < REALTIME_RETRY_BUDGET_SECONDS else None
    delay = SCHEDULED_RETRY_INITIAL_SECONDS * (2.0 ** max(attempt - 1, 0))
    return min(delay, SCHEDULED_RETRY_MAX_SECONDS)


#: Printed on most US health plan cards ahead of the payer's own member id
#: (the HIPAA-assigned issuer identifier for health plans). Payers reject a
#: member id that still carries it rather than stripping it themselves.
_CARD_ISSUER_PREFIX = "80840"


def strip_card_issuer_prefix(member_id: str) -> str:
    """``member_id`` without a leading card-issuer prefix, if it has one."""
    stripped = member_id.strip()
    if stripped.startswith(_CARD_ISSUER_PREFIX) and len(stripped) > len(_CARD_ISSUER_PREFIX):
        return stripped[len(_CARD_ISSUER_PREFIX) :]
    return stripped


def _normalize_name(name: str) -> str:
    """Loose enough to look past a hyphen, an apostrophe, or case."""
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def identity_matches_on_file(
    returned: EligibilitySubscriber, *, patient: Patient, coverage: PatientCoverage
) -> bool:
    """Whether a relaxed retry's answer is still about the client on file.

    An identity-relaxing retry (dropping the member id for a 72, trying a
    name variant for a 75) exists to get past a payer's typo-intolerant
    matching — never to accept whichever record the payer happens to
    return instead. On an explicit disagreement between what the payer
    echoed back and what is on file — a date of birth that does not match,
    or a name that does not resemble it — the answer must not be
    auto-accepted; it goes to a person instead of into the chart. A payer
    that echoes no demographics at all is not evidence either way and does
    not by itself block acceptance.
    """
    expected_last_name: str | None
    if coverage.subscriber_relationship == "self":
        expected_last_name = patient.last_name
        expected_dob = _wire_date(patient.date_of_birth)
    else:
        expected_last_name = coverage.subscriber_last_name
        expected_dob = _wire_date(coverage.subscriber_date_of_birth)

    if returned.dateOfBirth and expected_dob and returned.dateOfBirth != expected_dob:
        return False
    return not (
        returned.lastName
        and expected_last_name
        and _normalize_name(returned.lastName) != _normalize_name(expected_last_name)
    )


# ---------------------------------------------------------------------------
# Building a 270
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BillingIdentity:
    """Who the inquiry is from: the practice's billing NPI, or the clinician's own."""

    npi: str
    organization_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None

    def as_provider(self) -> EligibilityProvider:
        return EligibilityProvider(
            npi=self.npi,
            organizationName=self.organization_name,
            firstName=self.first_name,
            lastName=self.last_name,
        )


def load_billing_identity(session: Session, user: User) -> BillingIdentity | None:
    """The practice's billing NPI when one is on file, else the clinician's own.

    A group bills under its type-2 NPI and legal name; a solo practice under
    the rendering clinician's type-1 NPI and their name. ``None`` when
    neither is configured — the check cannot be asked without an NPI.
    """
    profile = load_billing_profile(session)
    billing_npi = profile.get("billing_npi")
    if isinstance(billing_npi, str) and billing_npi:
        legal_name = profile.get("legal_name")
        return BillingIdentity(
            npi=billing_npi,
            organization_name=legal_name if isinstance(legal_name, str) else user.name,
        )
    clinician = session.get(ClinicianProfileRow, user.id)
    if clinician is None or not clinician.npi_number:
        return None
    first, _, last = (user.legal_name or user.name or "").strip().rpartition(" ")
    return BillingIdentity(
        npi=clinician.npi_number, first_name=first or None, last_name=last or None
    )


def _wire_date(value: date | str | None) -> str | None:
    """``YYYYMMDD`` from a date or an ISO string."""
    if value is None:
        return None
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    return value.replace("-", "")[:8] or None


def _wire_sex(value: str | None) -> Any:
    return value if value in ("M", "F", "U") else None


def build_270(
    coverage: PatientCoverage, payer: Payer, patient: Patient, identity: BillingIdentity
) -> EligibilityRequest:
    """The inquiry for this coverage, asked about outpatient mental health.

    When the subscriber is the client, the client is the subscriber loop.
    Otherwise the subscriber's own details (from the coverage) go in the
    subscriber loop and the client is sent as the dependent the payer is
    asked about.
    """
    payer_id = payer.clearinghouse_payer_id or payer.payer_id
    if not payer_id or payer_id == UNKNOWN_PAYER_ID:
        msg = "The payer on file has no electronic payer id yet. Pick it from the payer directory."
        raise EligibilityNotPossibleError(msg)

    if coverage.subscriber_relationship == "self":
        subscriber = EligibilitySubscriber(
            memberId=strip_card_issuer_prefix(coverage.member_id),
            firstName=patient.first_name,
            lastName=patient.last_name,
            dateOfBirth=_wire_date(patient.date_of_birth),
            gender=_wire_sex(patient.sex),
            groupNumber=coverage.group_number,
        )
        dependents: list[EligibilityDependent] | None = None
    else:
        subscriber = EligibilitySubscriber(
            memberId=strip_card_issuer_prefix(coverage.member_id),
            firstName=coverage.subscriber_first_name,
            lastName=coverage.subscriber_last_name,
            dateOfBirth=_wire_date(coverage.subscriber_date_of_birth),
            gender=_wire_sex(coverage.subscriber_sex),
            groupNumber=coverage.group_number,
        )
        dependents = [
            EligibilityDependent(
                firstName=patient.first_name,
                lastName=patient.last_name,
                dateOfBirth=_wire_date(patient.date_of_birth),
                gender=_wire_sex(patient.sex),
            )
        ]

    return EligibilityRequest(
        tradingPartnerServiceId=payer_id,
        provider=identity.as_provider(),
        subscriber=subscriber,
        dependents=dependents,
        encounter=EligibilityEncounter(serviceTypeCodes=list(REQUESTED_SERVICE_TYPE_CODES)),
    )


# ---------------------------------------------------------------------------
# Running a check
# ---------------------------------------------------------------------------


@dataclass
class EligibilityDeps:
    """Everything one check needs, handed in so the route, the queued job
    and the tests assemble it their own way.

    ``session`` is optional and only used to raise the enrollment claim
    event on a ``stop_enrollment`` disposition (see
    :func:`reject_disposition`); a caller that leaves it unset simply does
    not get that event, the same as before this existed.
    """

    client: ClearinghouseClient | None
    identity: BillingIdentity | None
    coverage: PatientCoverageRepository
    payers: PayerRepository
    patients: PatientRepository
    session: Session | None = None


@dataclass(frozen=True, slots=True)
class EligibilityCheck:
    """One completed check: what was asked about, and what came back."""

    summary: EligibilitySummary
    coverage: PatientCoverage
    payer: Payer
    patient: Patient


class EligibilityCheckFailedError(Exception):
    """The inquiry was sent (or may have been) and no answer was stored.

    Wraps the adapter's typed error so the caller can audit the disclosure
    — the payer may well have received the 270 — before deciding whether
    the failure is worth a retry. ``cause`` is the adapter's exception.
    """

    def __init__(
        self, cause: ClearinghouseError, coverage: PatientCoverage, patient: Patient
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.coverage = coverage
        self.patient = patient


def _enrollment_required_event(
    coverage: PatientCoverage,
    payer: Payer,
    user_id: str,
    aaa_errors: list[AaaError],
    occurred_at: datetime,
) -> ClaimEvent:
    """The claim event a ``stop_enrollment`` AAA rejection raises.

    An eligibility check has no claim, so ``claim_id`` and
    ``control_number`` are synthesized the way ``app.claims.enrollment``
    does for its own enrollment events: enough for a person to find the
    coverage, nothing from the 271 itself.
    """
    return ClaimEvent(
        kind="enrollment_action_required",
        control_number=coverage.id,
        claim_id=f"{payer.payer_id}:eligibility",
        user_id=user_id,
        payer_id=payer.payer_id,
        payer_name=payer.name,
        state=aaa_errors[0].code,
        occurred_at=occurred_at,
        detail=ClaimEventDetail(
            codes=tuple(
                CodeRef(system="edit", code=e.code, description=e.description) for e in aaa_errors
            )
        ),
    )


def _raise_enrollment_task_if_needed(  # noqa: PLR0913 — the event's fields, keyword-only
    *,
    session: Session | None,
    coverage: PatientCoverage,
    payer: Payer,
    user_id: str,
    summary: EligibilitySummary,
    checked_at: datetime,
) -> None:
    """Route a provider-enrollment AAA rejection to the enrollment surface.

    41/43/51 are not "eligibility failed" — the practice isn't enrolled
    with this payer, and that is a task for whoever manages enrollments,
    not a note on the clinician's coverage card. When ``session`` is given,
    that task is the same claim-events surface ``app.claims.enrollment``
    already uses for a payer's own enrollment refusals.
    """
    if session is None or not summary.aaa_errors:
        return
    codes = [e.code for e in summary.aaa_errors]
    if reject_disposition(codes) != "stop_enrollment":
        return
    emit(
        session,
        _enrollment_required_event(coverage, payer, user_id, summary.aaa_errors, checked_at),
    )


def run_eligibility(coverage_id: str, user: User, deps: EligibilityDeps) -> EligibilityCheck:
    """Ask the payer about this coverage and store the 271 on the row.

    The check tells the payer who the client is. That is a disclosure of the
    client to a third party, and the caller audits it — see
    :func:`eligibility_audit_changes` — whether the payer answered, refused,
    or never replied. The 271 itself lives on the coverage row and nowhere
    else; nothing here logs it.

    Raises :class:`CoverageNotFoundError` when the coverage or its client is
    not visible to ``user``, :class:`EligibilityNotPossibleError` when the
    practice cannot ask (no credentials, no NPI, no payer id) — in both
    cases nothing was sent — and :class:`EligibilityCheckFailedError` around
    the adapter's typed error when the call itself failed.
    """
    coverage = deps.coverage.get(coverage_id)
    if coverage is None:
        raise CoverageNotFoundError(coverage_id)
    patient = deps.patients.get(coverage.patient_id, user.id)
    if patient is None:
        raise CoverageNotFoundError(coverage_id)
    payer = deps.payers.get(coverage.payer_id)
    if payer is None:
        raise CoverageNotFoundError(coverage_id)

    if deps.client is None:
        msg = "Eligibility checks are not set up: no clearinghouse account is configured."
        raise EligibilityNotPossibleError(msg)
    if deps.identity is None:
        msg = "Eligibility checks need a billing NPI or the clinician's NPI on file."
        raise EligibilityNotPossibleError(msg)

    inquiry = build_270(coverage, payer, patient, deps.identity)
    try:
        response = deps.client.check_eligibility(inquiry)
    except ClearinghouseError as exc:
        raise EligibilityCheckFailedError(exc, coverage, patient) from exc
    checked_at = utc_now()
    summary = summarize_271(response, checked_at=checked_at)

    stored = deps.coverage.update(
        coverage.model_copy(
            update={
                "last_271": response.model_dump(mode="json", exclude_none=True),
                "verified_at": checked_at,
            }
        )
    )
    logger.info(
        "eligibility_checked coverage_id=%s payer_id=%s status=%s",
        coverage.id,
        inquiry.tradingPartnerServiceId,
        summary.status,
    )
    _raise_enrollment_task_if_needed(
        session=deps.session,
        coverage=stored,
        payer=payer,
        user_id=user.id,
        summary=summary,
        checked_at=checked_at,
    )
    return EligibilityCheck(summary=summary, coverage=stored, payer=payer, patient=patient)


def eligibility_audit_changes(
    coverage: PatientCoverage,
    trigger: EligibilityTrigger,
    *,
    summary: EligibilitySummary | None = None,
    failure: str | None = None,
) -> dict[str, Any]:
    """The audit row's ``changes`` for one check: ids, trigger, outcome.

    ``summary`` for a check the payer answered (an AAA refusal included);
    ``failure`` names the adapter error class when it did not. Nothing off
    the card and nothing from the 271 beyond its status and AAA codes.
    """
    changes: dict[str, Any] = {
        "coverage_id": coverage.id,
        "payer_id": coverage.payer_id,
        "trigger": trigger,
    }
    if summary is not None:
        changes["status"] = summary.status
        changes["aaa_codes"] = [e.code for e in summary.aaa_errors]
        changes["carveout"] = summary.carveout_administrator is not None
    if failure is not None:
        changes["status"] = "failed"
        changes["failure"] = failure
    return changes


def eligibility_actor_type(trigger: EligibilityTrigger) -> str:
    """The re-verify button is the clinician acting; every other trigger is the system."""
    return ACTOR_TYPE_CLINICIAN if trigger == "manual" else ACTOR_TYPE_SYSTEM


# ---------------------------------------------------------------------------
# Queuing a check after a save
# ---------------------------------------------------------------------------


def schedule_eligibility_check(coverage_id: str, user_id: str, trigger: EligibilityTrigger) -> None:
    """Queue one check on the post-save task queue.

    Opaque ids only in the payload (see ``app.services.cloud_tasks_service``):
    the worker re-resolves the tenant from ``user_id``. No dedup key on
    purpose — one check per trigger is the rule, and a second save within
    the queue's dedup window is a second trigger.
    """
    from ..jobs.task_queue import enqueue  # noqa: PLC0415 — lazy; see task_queue's own note
    from ..settings import get_settings  # noqa: PLC0415

    enqueue(
        get_settings().eligibility_check_task_queue,
        ELIGIBILITY_JOB_PATH,
        {"coverage_id": coverage_id, "user_id": user_id, "trigger": trigger},
    )


class EligibilityAutoCheck:
    """The post-save hook: queues a check when the practice has it switched on.

    Built per request by :func:`get_eligibility_auto_check` and injected, so
    the routes that save coverage do not read settings or touch the queue
    themselves and the tests hand in a recorder.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        schedule: Callable[[str, str, EligibilityTrigger], None] = schedule_eligibility_check,
    ) -> None:
        self.enabled = enabled
        self._schedule = schedule

    def __call__(self, coverage_id: str, user_id: str, trigger: EligibilityTrigger) -> bool:
        """Queue the check if enabled; returns whether it was."""
        if not self.enabled:
            return False
        self._schedule(coverage_id, user_id, trigger)
        return True


def get_eligibility_auto_check() -> EligibilityAutoCheck:
    from ..db import get_db_session  # noqa: PLC0415 — request-scoped, resolved at call time
    from ..services.practice_billing_profile import (  # noqa: PLC0415
        eligibility_auto_check_enabled,
    )

    return EligibilityAutoCheck(enabled=eligibility_auto_check_enabled(get_db_session()))
