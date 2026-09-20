# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What the chart says about the patient, to the patient.

Two routes, both on the patient principal and both about one row — the
caller's own:

* ``GET /api/patient/profile`` — the chart's demographics, as their subject
  may see them.
* ``PATCH /api/patient/profile`` — the parts of that the subject may change.

**No patient id anywhere.** Not in the path, not in the body, not in a query
parameter. The id comes off the authenticated principal, which is what makes
these routes structurally free of the IDOR shape: there is no id to compare,
so there is nothing to forget to compare. ``rls_patient_self_read`` and
``rls_patient_self_write`` back that up underneath, and back it up rather
than provide it.

**Read and write are allow-listed separately, and neither is the ORM row.**
What comes back is :class:`~app.models.patient_facing.PatientFacingPatient`,
whose every field is a recorded decision in
``app.models.patient_facing`` and whose shape is pinned by a test — a column
added to ``patients`` tomorrow is invisible here until somebody writes down
what happens to it. What may be written is narrower still and is listed in
``PATIENT_SELF_WRITABLE_COLUMNS``: contact details and a preferred name.

**Identity is read-only, and that is a product decision rather than a
technical one.** A patient whose legal name or date of birth is wrong on the
chart is telling the practice something — a claim was filed under the wrong
name, or a record belongs to somebody else — and the resolution involves a
person looking at it. The intake form already collects exactly that
correction, with the clinician on the other end. So the request model
refuses those fields outright rather than accepting and ignoring them: a
form that silently drops a change is worse than one that says no.

**Contact changes take effect immediately, including for recovery.** The
patient who controls the session controls where the next sign-in link goes.
That is the trust model of every portal, and the alternative — a pending
value a clinician confirms — means a patient who changed email providers
cannot recover, which is the case recovery exists for. What it costs is
recorded rather than mitigated: the change is audited as its own event, with
hashes of the old and new values, so it shows up on the chart's activity
where a clinician sees it.

Nothing here logs a field value. The audit payload carries the names of the
fields that changed, and for the two delivery channels a pair of salted
digests — never an address, a number or a name.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..api_errors import ForbiddenError, NotFoundError
from ..auth.patient_context import AuthStrength, PatientContext, get_patient_context
from ..auth.route_access import subscription_exempt
from ..models.audit import AuditAction, ResourceType
from ..models.patient_facing import PATIENT_CONTACT_CHANNEL_COLUMNS, PatientFacingPatient
from ..models.validators import validate_email, validate_phone
from ..repositories import get_patient_repository

# Runtime import: FastAPI resolves this annotation when it builds the route,
# so it cannot live in a TYPE_CHECKING block.
from ..repositories.patient import PatientRepository  # noqa: TC001
from ..services.audit_service import AuditService, get_audit_service
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/patient", tags=["patient-profile"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]

#: Same posture as the rest of the patient-facing surface: a patient does
#: not hold the practice's subscription and cannot see or fix its billing
#: state, so their own demographics should not fail for it.
_SUBSCRIPTION_EXEMPT = Depends(subscription_exempt)


class UpdatePatientProfileRequest(BaseModel):
    """The fields a patient may change about themselves.

    ``extra="forbid"`` is doing real work. Without it, a request naming
    ``first_name`` would be accepted, ignored, and answered ``200`` with the
    unchanged name — a patient told their correction saved when it did not.
    With it, the same request is a 422 that names the field, which is an
    answer a screen can turn into "ask your practice to change this".

    Every field is optional and ``None`` means "set it to empty", not "leave
    it alone" — an address line a patient clears should clear. Which fields
    were named at all comes from ``model_fields_set``, so the two are
    distinguishable.
    """

    model_config = ConfigDict(extra="forbid")

    preferred_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=2)
    postal_code: str | None = Field(default=None, max_length=10)

    @field_validator("email")
    @classmethod
    def validate_email_field(cls, v: str | None) -> str | None:
        """Same validator the clinician-facing write uses.

        A patient must not be able to put a value on the chart that the
        chart's own surfaces would have refused — and an unparseable address
        here is a delivery channel that silently stops working.
        """
        return validate_email(v)

    @field_validator("phone")
    @classmethod
    def validate_phone_field(cls, v: str | None) -> str | None:
        return validate_phone(v)


def _require_stepped_up(patient: PatientContext) -> None:
    """Refuse a single-factor principal on the WRITE.

    The read is stepped up too (demographics are the chart), but the write
    is the one this exists to argue about: changing the email address is
    changing where the next sign-in link goes, so a link that reached the
    wrong inbox must not be enough to redirect the one after it.
    """
    if patient.auth_strength is not AuthStrength.STEPPED_UP:
        raise ForbiddenError("Confirm it is you to continue.", code="STEP_UP_REQUIRED")


def _contact_handle(value: str | None) -> str | None:
    """A salted digest of one contact value, for the audit row.

    Keyed on the deployment's portal signing key: stable within a
    deployment, so "was it changed back to the old one?" is answerable, and
    meaningless outside it. Unkeyed, a digest of a phone number is the phone
    number — the space is small enough to enumerate in seconds — and the
    audit log is not where a second copy of someone's contact book belongs.

    ``None`` stays ``None``: that a value was cleared is not a secret, and
    hashing the empty string would produce one constant that gave it away
    anyway.
    """
    if value is None or value == "":
        return None
    key = get_settings().portal_token_signing_key.get_secret_value().encode()
    return hmac.new(key, value.strip().lower().encode(), hashlib.sha256).hexdigest()[:16]


@router.get(
    "/profile",
    response_model=PatientFacingPatient,
    dependencies=[_SUBSCRIPTION_EXEMPT],
)
def get_profile(
    request: Request,
    patient: CurrentPatient,
    patients: Annotated[PatientRepository, Depends(get_patient_repository)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> PatientFacingPatient:
    """The chart's demographics for the calling patient.

    A read of the record, so it goes on the audit log the same way the
    intake form's identity read does. ``404`` when there is no live row,
    which for a principal resolved off a live session means the chart was
    deleted underneath them.
    """
    _require_stepped_up(patient)

    own = patients.get_for_patient_principal(patient.patient_id)
    if own is None:
        raise NotFoundError("Record not found")

    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_VIEWED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT,
        resource_id=patient.patient_id,
    )
    return own


@router.patch(
    "/profile",
    response_model=PatientFacingPatient,
    dependencies=[_SUBSCRIPTION_EXEMPT],
)
def update_profile(
    body: UpdatePatientProfileRequest,
    request: Request,
    patient: CurrentPatient,
    patients: Annotated[PatientRepository, Depends(get_patient_repository)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> PatientFacingPatient:
    """Change the caller's own contact details or preferred name.

    Only the fields the request actually named are written, so a screen that
    edits one line does not blank the rest of the form. Writing nothing is a
    valid no-op and answers with the unchanged profile.

    Two audit rows on a contact change rather than one, because they answer
    different questions: ``PATIENT_PROFILE_UPDATED`` says the patient edited
    their profile and which fields moved, and
    ``PATIENT_PROFILE_CONTACT_CHANGED`` says a delivery channel moved and
    lets one value be tested against the record afterwards.
    """
    _require_stepped_up(patient)

    before = patients.get_for_patient_principal(patient.patient_id)
    if before is None:
        raise NotFoundError("Record not found")

    named = body.model_fields_set
    changes: dict[str, str | None] = {
        field: getattr(body, field)
        for field in named
        if getattr(body, field) != getattr(before, field, None)
    }
    if not changes:
        return before

    after = patients.update_contact_for_patient_principal(patient.patient_id, changes)
    if after is None:
        raise NotFoundError("Record not found")

    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_PROFILE_UPDATED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT,
        resource_id=patient.patient_id,
        # Field names, never values. An address is PHI-adjacent and it
        # already lives on the row this write just touched.
        changes={"fields": sorted(changes)},
    )

    moved_channels = sorted(set(changes) & PATIENT_CONTACT_CHANNEL_COLUMNS)
    if moved_channels:
        audit.log_patient_principal_action(
            action=AuditAction.PATIENT_PROFILE_CONTACT_CHANGED,
            request=request,
            patient_id=patient.patient_id,
            resource_type=ResourceType.PATIENT,
            resource_id=patient.patient_id,
            changes={
                field: {
                    "old": _contact_handle(getattr(before, field)),
                    "new": _contact_handle(getattr(after, field)),
                }
                for field in moved_channels
            },
        )
    return after
