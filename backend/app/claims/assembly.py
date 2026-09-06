# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Turning a session into a draft claim.

The appointment carries the visit codes, the client and the appointment
type carry the rate, the coverage carries the plan, the billing profile and
the clinician's profile carry who is filing. :func:`build_claim_from_session`
reads all of them once and writes a ``draft`` claim that copies every value
it needs — a snapshot, so a later edit to any source leaves the claim as it
was. The correction path (:func:`build_corrected_claim`) is the same
assembly run again against the sources as they stand now, producing a
child claim that names its parent; a void (:func:`build_void_claim`) copies
the parent instead, since a void must restate the claim being cancelled.

Nothing here validates. The assembly writes what is on file, including a
zero charge when no rate is set and an empty diagnosis list when none was
recorded; :mod:`app.claims.scrub` is where those become findings.

The charge resolves exactly the way the charge action and the unbilled
queue resolve it — ``resolve_rate_cents`` over the client's override and
the appointment type's default — so the claim, the receipt and the queue
never disagree about the fee.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Literal

from ..models.claims import (
    AddOnService,
    BillingProviderSnapshot,
    BillingSnapshot,
    Claim,
    ClaimLine,
    PersonSnapshot,
    RenderingProviderSnapshot,
    SubscriberSnapshot,
)
from ..scheduling_engine.services.rate_resolver import resolve_rate_cents
from ..utcnow import utc_now
from .validation import MAX_DX_POINTERS_PER_LINE

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import tzinfo

    from ..models import User
    from ..models.claims import FrequencyCode
    from ..models.coverage import PatientCoverage, Payer
    from ..models.patient import Patient
    from ..repositories.clinician_profile import ClinicianProfile, ClinicianProfileRepository
    from ..repositories.coverage import PatientCoverageRepository, PayerRepository
    from ..repositories.patient import PatientRepository
    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.models.appointment_type import AppointmentType
    from ..scheduling_engine.repositories.appointment import AppointmentRepository
    from ..scheduling_engine.repositories.appointment_type import AppointmentTypeRepository


class ClaimBuildError(Exception):
    """The claim cannot be built from what is on file."""


class AppointmentNotFoundError(ClaimBuildError):
    """No such appointment, or not one this clinician can see."""


class ClientNotFoundError(ClaimBuildError):
    """The appointment's client is absent or not granted to this clinician."""


class NoActiveCoverageError(ClaimBuildError):
    """The client has no active coverage on file to build the claim against."""


class PayerNotFoundError(ClaimBuildError):
    """The coverage names a payer that is no longer on the practice's list."""


#: Crockford's base32 alphabet: digits and upper-case letters with the four
#: easily-confused letters (I, L, O, U) left out. Every character is in the
#: X12 basic character set.
_CONTROL_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CONTROL_RANDOM_LENGTH = 6

#: Line 1 is always the visit's own service; line 2, when present, is the
#: psychotherapy add-on delivered alongside it.
_VISIT_LINE_NUMBER = 1
_ADD_ON_LINE_NUMBER = 2


def new_control_number(now: datetime | None = None) -> str:
    """A fresh claim control number (CLM01).

    Two parts: the build instant in base32, so numbers sort by when the
    claim was made, then six random characters, so they are not guessable
    and two claims built in the same second do not collide. Thirteen
    characters, inside the 17 the clearinghouse allows, never reused —
    the ``claims.control_number`` unique constraint is the backstop.
    """
    stamp = int((now or utc_now()).timestamp())
    prefix = ""
    while stamp:
        prefix = _CONTROL_ALPHABET[stamp % len(_CONTROL_ALPHABET)] + prefix
        stamp //= len(_CONTROL_ALPHABET)
    suffix = "".join(secrets.choice(_CONTROL_ALPHABET) for _ in range(_CONTROL_RANDOM_LENGTH))
    return f"{prefix}{suffix}"


def line_control_number(control_number: str, line_number: int) -> str:
    """The line's own control number (REF*6R), derived from the claim's."""
    return f"{control_number}L{line_number}"


@dataclass(frozen=True)
class ClaimSources:
    """Everything the assembly reads from, all scoped to the request's tenant.

    ``billing_profile`` is a callable rather than a value so the singleton
    row is read only when a claim is actually built. ``timezone`` is the
    practice's: the service date on a claim is the local calendar date the
    visit happened, not the UTC date of its start instant.
    """

    appointments: AppointmentRepository
    appointment_types: AppointmentTypeRepository
    patients: PatientRepository
    coverage: PatientCoverageRepository
    payers: PayerRepository
    clinician_profiles: ClinicianProfileRepository
    billing_profile: Callable[[], Mapping[str, object]]
    timezone: tzinfo


def build_claim_from_session(
    appointment_id: str,
    user: User,
    sources: ClaimSources,
    *,
    add_on: AddOnService | None = None,
    now: datetime | None = None,
) -> Claim:
    """A ``draft`` claim for the visit ``appointment_id`` was.

    Reads the appointment, its client, the client's active coverage and its
    payer, the appointment type (for the rate), the clinician's profile
    (for the rendering NPI and taxonomy) and the practice's billing
    profile, and snapshots them into one claim with one line — two when
    ``add_on`` names a psychotherapy add-on delivered alongside the visit.

    Raises a :class:`ClaimBuildError` subclass naming what is missing.
    """
    return _build(appointment_id, user, sources, add_on=add_on, now=now)


def build_corrected_claim(
    parent: Claim,
    user: User,
    sources: ClaimSources,
    *,
    now: datetime | None = None,
) -> Claim:
    """A replacement (frequency ``7``) for ``parent``, rebuilt from today's sources.

    The point of a correction is to pick up what was fixed on the
    appointment, the coverage or the billing profile since the parent was
    built, so this is the original assembly run again — not a copy of the
    parent — with the parent named as lineage. The add-on line, if the
    parent had one, is carried across as it was.
    """
    original = next((line for line in parent.lines if line.line_number == _VISIT_LINE_NUMBER), None)
    if original is None or original.appointment_id is None:
        msg = "The parent claim does not name the appointment it was built from."
        raise AppointmentNotFoundError(msg)
    add_on = _add_on_from(parent)
    return _build(
        original.appointment_id,
        user,
        sources,
        add_on=add_on,
        now=now,
        frequency_code="7",
        parent_claim_id=parent.id,
    )


def build_void_claim(parent: Claim, *, now: datetime | None = None) -> Claim:
    """A void (frequency ``8``) of ``parent``: the same claim, restated to cancel it.

    A void tells the payer to reverse a claim, so it must carry the claim
    being reversed as it was filed — the parent's snapshots and lines,
    copied, under a new control number and a fresh ``draft`` state.
    """
    built_at = now or utc_now()
    claim_id = str(uuid.uuid4())
    control = new_control_number(built_at)
    lines = [
        line.model_copy(
            update={
                "id": str(uuid.uuid4()),
                "claim_id": claim_id,
                "line_control_number": line_control_number(control, line.line_number),
                "allowed_cents": None,
                "paid_cents": 0,
                "patient_resp_cents": None,
                "adjustments": None,
                "created_at": built_at,
            }
        )
        for line in parent.lines
    ]
    return parent.model_copy(
        update={
            "id": claim_id,
            "control_number": control,
            "state": "draft",
            "frequency_code": "8",
            "parent_claim_id": parent.id,
            "total_paid_cents": 0,
            "submitted_at": None,
            "payer_accepted_at": None,
            "adjudicated_at": None,
            "created_at": built_at,
            "updated_at": built_at,
            "lines": lines,
        }
    )


def _add_on_from(parent: Claim) -> AddOnService | None:
    second = next((line for line in parent.lines if line.line_number == _ADD_ON_LINE_NUMBER), None)
    if second is None:
        return None
    return AddOnService(cpt=second.cpt, charge_cents=second.charge_cents)


def _build(  # noqa: PLR0913 — the loader's inputs, keyword-only past the id
    appointment_id: str,
    user: User,
    sources: ClaimSources,
    *,
    add_on: AddOnService | None,
    now: datetime | None,
    frequency_code: FrequencyCode = "1",
    parent_claim_id: str | None = None,
) -> Claim:
    appointment = sources.appointments.get(appointment_id, user.id)
    if appointment is None:
        raise AppointmentNotFoundError(appointment_id)
    patient = sources.patients.get(appointment.patient_id, user.id)
    if patient is None:
        raise ClientNotFoundError(appointment.patient_id)
    coverage = sources.coverage.get_active(patient.id)
    if coverage is None:
        raise NoActiveCoverageError(patient.id)
    payer = sources.payers.get(coverage.payer_id)
    if payer is None:
        raise PayerNotFoundError(coverage.payer_id)
    appointment_type = None
    if appointment.appointment_type_id is not None:
        appointment_type = sources.appointment_types.get(appointment.appointment_type_id, user.id)
    return assemble_draft(
        appointment=appointment,
        patient=patient,
        coverage=coverage,
        payer=payer,
        appointment_type=appointment_type,
        billing_profile=sources.billing_profile(),
        rendering_provider=sources.clinician_profiles.get(user.id),
        user=user,
        timezone=sources.timezone,
        add_on=add_on,
        now=now,
        frequency_code=frequency_code,
        parent_claim_id=parent_claim_id,
    )


def assemble_draft(  # noqa: PLR0913 — every source row the claim snapshots, keyword-only
    *,
    appointment: Appointment,
    patient: Patient,
    coverage: PatientCoverage,
    payer: Payer,
    appointment_type: AppointmentType | None,
    billing_profile: Mapping[str, object],
    rendering_provider: ClinicianProfile | None,
    user: User,
    timezone: tzinfo,
    add_on: AddOnService | None = None,
    now: datetime | None = None,
    frequency_code: FrequencyCode = "1",
    parent_claim_id: str | None = None,
) -> Claim:
    """The pure heart of the assembly: rows in, a ``draft`` claim out.

    No I/O and no clock beyond ``now``; :func:`build_claim_from_session` is
    the loader in front of it.
    """
    built_at = now or utc_now()
    claim_id = str(uuid.uuid4())
    control = new_control_number(built_at)
    service_date = appointment.start_at.astimezone(timezone).date()
    diagnosis_codes = list(appointment.diagnosis_codes or [])
    pointers = list(range(1, min(len(diagnosis_codes), MAX_DX_POINTERS_PER_LINE) + 1))
    telehealth = bool(appointment.video_link)

    def line(number: int, cpt: str, units: int, charge_cents: int) -> ClaimLine:
        return ClaimLine(
            id=str(uuid.uuid4()),
            claim_id=claim_id,
            patient_id=patient.id,
            appointment_id=appointment.id,
            line_number=number,
            line_control_number=line_control_number(control, number),
            service_date=service_date,
            cpt=cpt,
            modifiers=list(appointment.modifiers or []),
            units=units,
            charge_cents=charge_cents,
            dx_pointers=list(pointers),
            telehealth=telehealth,
            created_at=built_at,
        )

    lines = [
        line(
            _VISIT_LINE_NUMBER,
            (appointment.service_code or "").strip(),
            appointment.unit_count or 1,
            resolve_rate_cents(patient.rate_cents, appointment_type) or 0,
        )
    ]
    if add_on is not None:
        lines.append(line(_ADD_ON_LINE_NUMBER, add_on.cpt, 1, add_on.charge_cents))

    return Claim(
        id=claim_id,
        control_number=control,
        patient_id=patient.id,
        coverage_id=coverage.id,
        payer_id=payer.id,
        state="draft",
        frequency_code=frequency_code,
        parent_claim_id=parent_claim_id,
        total_charge_cents=sum(line.charge_cents for line in lines),
        total_paid_cents=0,
        diagnosis_codes=diagnosis_codes,
        place_of_service=appointment.place_of_service,
        billing_snapshot=_billing_snapshot(billing_profile, rendering_provider, user),
        subscriber_snapshot=_subscriber_snapshot(patient, coverage, payer),
        created_at=built_at,
        updated_at=built_at,
        lines=lines,
    )


def _billing_snapshot(
    billing_profile: Mapping[str, object],
    rendering_provider: ClinicianProfile | None,
    user: User,
) -> BillingSnapshot:
    rendering_npi = rendering_provider.npi_number if rendering_provider is not None else None
    first_name, last_name = _split_name(user.legal_name or user.name)
    return BillingSnapshot(
        billing_provider=BillingProviderSnapshot(
            legal_name=_text(billing_profile.get("legal_name")),
            tax_id_last4=_text(billing_profile.get("tax_id_last4")),
            tax_id_type=_tax_id_type(billing_profile.get("tax_id_type")),
            # A solo practice bills under its clinician's own NPI.
            npi=_text(billing_profile.get("billing_npi")) or rendering_npi,
            address_line1=_text(billing_profile.get("address_line1")),
            address_line2=_text(billing_profile.get("address_line2")),
            city=_text(billing_profile.get("city")),
            state=_text(billing_profile.get("state")),
            postal_code=_text(billing_profile.get("postal_code")),
            phone=_text(billing_profile.get("phone")),
        ),
        rendering_provider=RenderingProviderSnapshot(
            user_id=user.id,
            first_name=first_name,
            last_name=last_name,
            npi=rendering_npi,
            taxonomy_code=(
                rendering_provider.taxonomy_code if rendering_provider is not None else None
            ),
        ),
    )


def _subscriber_snapshot(
    patient: Patient, coverage: PatientCoverage, payer: Payer
) -> SubscriberSnapshot:
    client = PersonSnapshot(
        first_name=patient.first_name,
        last_name=patient.last_name,
        date_of_birth=_iso_date(patient.date_of_birth),
        sex=_sex(patient.sex),
        address_line1=patient.address_line1,
        address_line2=patient.address_line2,
        city=patient.city,
        state=patient.state,
        postal_code=patient.postal_code,
        phone=patient.phone,
    )
    if coverage.subscriber_relationship == "self":
        subscriber = client.model_copy()
    else:
        subscriber = PersonSnapshot(
            first_name=coverage.subscriber_first_name,
            last_name=coverage.subscriber_last_name,
            date_of_birth=coverage.subscriber_date_of_birth,
            sex=coverage.subscriber_sex,
            address_line1=coverage.subscriber_address_line1,
            address_line2=coverage.subscriber_address_line2,
            city=coverage.subscriber_city,
            state=coverage.subscriber_state,
            postal_code=coverage.subscriber_postal_code,
        )
    return SubscriberSnapshot(
        member_id=coverage.member_id,
        group_number=coverage.group_number,
        plan_name=coverage.plan_name,
        relationship=coverage.subscriber_relationship,
        coverage_active=coverage.active,
        payer_id=payer.payer_id,
        payer_name=payer.name,
        subscriber=subscriber,
        patient=client,
    )


def _split_name(full_name: str | None) -> tuple[str | None, str | None]:
    """First and last name from a display name, credentials dropped.

    "Jane Q. Smith, LCSW" gives ("Jane", "Q. Smith"). A single word is a
    first name with no last name; the scrub reports the gap.
    """
    if not full_name:
        return None, None
    bare = full_name.split(",", 1)[0].strip()
    parts = bare.split()
    if not parts:
        return None, None
    return parts[0], (" ".join(parts[1:]) or None)


def _iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _tax_id_type(value: object) -> Literal["ein", "ssn"] | None:
    if value == "ein":
        return "ein"
    if value == "ssn":
        return "ssn"
    return None


def _sex(value: str | None) -> Literal["M", "F", "U"] | None:
    if value == "M":
        return "M"
    if value == "F":
        return "F"
    if value == "U":
        return "U"
    return None
