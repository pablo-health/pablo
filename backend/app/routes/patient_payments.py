# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Self-pay card payments: keep a client's card on file and charge it.

A practice charges its own clients with its own Stripe account. Nothing here
holds money on anyone's behalf, and no card number ever reaches this process:
the browser posts the card straight to Stripe against the SetupIntent this
module returns, and what comes back and gets stored is an opaque payment-method
id plus the brand, last four digits and expiry the UI renders.

Routes
------

* ``POST /api/patients/{patient_id}/payment-method/setup`` — mint (or reuse)
  the client's Stripe customer and return a SetupIntent client secret for
  Stripe.js to confirm in the browser.
* ``POST /api/patients/{patient_id}/payment-method`` — complete setup: read the
  confirmed SetupIntent back from Stripe and store what actually got attached.
* ``GET /api/patients/{patient_id}/payment-method`` — what is on file, so the
  charge button knows whether to enable itself.
* ``GET /api/patients/{patient_id}/charge-amount`` — what a charge would come
  to, so the clinician sees the figure before authorising it rather than after.
* ``POST /api/patients/{patient_id}/charges`` — charge the card on file. One
  click, one charge: there is no scheduler, nothing charges on session
  completion, and a decline is never retried automatically.
* ``GET /api/patients/{patient_id}/charges`` — the ledger for one client.

Write-before-money ordering (the load-bearing bit)
--------------------------------------------------

The charge route commits twice before a cent can move, and both matter:

1. The ledger row is written ``pending`` and committed before Stripe is called
   at all, so an attempt that dies mid-flight still leaves a row saying "we
   tried" for a human to reconcile. Calling Stripe first and writing on success
   would lose exactly the cases somebody needs to look at.
2. The PaymentIntent is created **unconfirmed**, its id is stamped onto that
   row and committed, and only then is the intent confirmed. The extra round
   trip buys one guarantee: every PaymentIntent that could possibly have moved
   money is one already written down. Confirming inside the create call would
   let a timeout leave Stripe holding a completed payment whose id was never
   learned, and a ledger row stuck at ``pending`` with nothing linking it to
   the money.

A decline is terminal — the row stays ``failed`` with the decline code in
``status_detail``, and retrying is a fresh, explicit charge.

Access
------

The client must be one the caller can see. That is decided by reading them
through the request's tenant-scoped repository: a client in another practice's
schema is not there at all, and one this clinician holds no grant on is hidden
by the row policy. Either way the answer is **404, never 403** — a 403 would
confirm the id exists.

What crosses to Stripe is an amount and opaque ids. The customer object carries
the client id so a human can match a Stripe customer to a chart; the
PaymentIntent carries the ledger row id, the acting clinician and the practice,
which is what lets the webhook tell this application's charges apart from the
ones the practice raises in its own Stripe dashboard. Clinical content never
crosses, and the log lines here carry opaque ids and amounts — never a name.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..db.models import DEFAULT_CHARGE_CURRENCY
from ..models.audit import AuditAction, ResourceType
from ..models.payments import (
    CardOnFile,
    CardOnFileResponse,
    CardSetupConfirmation,
    CardSetupResponse,
    ChargeAmountResponse,
    ChargeResponse,
    CreateChargeRequest,
    PatientCharge,
)
from ..payments.provider import PaymentCredentials, get_payment_credential_provider
from ..payments.reconcile import METADATA_CHARGE_ID, METADATA_PRACTICE_ID, METADATA_USER_ID
from ..payments.stripe_api import payment_intent_request, stripe_request
from ..repositories import (
    get_appointment_repository,
    get_appointment_type_repository,
    get_patient_payment_repository,
    get_patient_repository,
)
from ..scheduling_engine.services.rate_resolver import resolve_rate_cents
from ..services import AuditService, get_audit_service

if TYPE_CHECKING:
    from ..models import Patient, User
    from ..repositories.patient import PatientRepository
    from ..repositories.patient_payment import PatientPaymentRepository
    from ..scheduling_engine.repositories.appointment import AppointmentRepository
    from ..scheduling_engine.repositories.appointment_type import AppointmentTypeRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/patients", tags=["patient-payments"])

# Repositories are declared as dependencies rather than called inline, matching
# the rest of the route layer: the wiring is visible in each signature and a
# test can swap any of them without patching module globals.
PaymentsRepo = Annotated["PatientPaymentRepository", Depends(get_patient_payment_repository)]
PatientsRepo = Annotated["PatientRepository", Depends(get_patient_repository)]
AppointmentsRepo = Annotated["AppointmentRepository", Depends(get_appointment_repository)]
AppointmentTypesRepo = Annotated[
    "AppointmentTypeRepository", Depends(get_appointment_type_repository)
]
CurrentUser = Annotated["User", Depends(require_baa_acceptance)]
Tenant = Annotated[TenantContext, Depends(get_tenant_context)]


def _to_charge_response(charge: PatientCharge) -> ChargeResponse:
    return ChargeResponse(
        id=charge.id,
        amount_cents=charge.amount_cents,
        currency=charge.currency,
        status=charge.status,
        status_detail=charge.status_detail,
        appointment_id=charge.appointment_id,
        created_at=charge.created_at,
        updated_at=charge.updated_at,
    )


def _to_card_response(card: CardOnFile) -> CardOnFileResponse:
    return CardOnFileResponse(
        brand=card.card_brand,
        last4=card.card_last4,
        exp_month=card.card_exp_month,
        exp_year=card.card_exp_year,
        chargeable=card.chargeable,
    )


def _require_patient(patients: PatientRepository, patient_id: str, user_id: str) -> Patient:
    """404 unless this client is visible to this clinician in this practice.

    A client in another practice's schema is absent from the tenant-scoped
    repository; one this clinician holds no grant on is filtered out by the row
    policy. Both collapse to "not found" on purpose — a 403 would confirm the
    id exists.
    """
    patient = patients.get(patient_id, user_id)
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found.")
    return patient


def _require_credentials(practice_id: str | None) -> PaymentCredentials:
    """The Stripe credentials this practice charges with, or 503.

    503 rather than 403: nothing is forbidden, the deployment simply has no
    card processing configured for this practice yet.
    """
    credentials = get_payment_credential_provider().credentials_for_practice(practice_id)
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Card payments are not configured.",
        )
    return credentials


def _effective_rate_cents(
    patient: Patient,
    appointment_id: str | None,
    user_id: str,
    appointments: AppointmentRepository,
    appointment_types: AppointmentTypeRepository,
) -> int | None:
    """What this client's sessions cost: their own rate, else the type's default.

    ``None`` when neither is set — a legitimate answer meaning "nobody has said
    what this costs", never zero. The precedence itself lives in one place
    (:func:`resolve_rate_cents`) and is not re-derived here.

    Shared by the charge route and the preview so the figure a clinician is
    shown is the figure that would actually be charged, rather than two
    derivations that can drift apart.
    """
    appointment_type = None
    if appointment_id is not None:
        appointment = appointments.get(appointment_id, user_id)
        if appointment is not None and appointment.appointment_type_id is not None:
            appointment_type = appointment_types.get(appointment.appointment_type_id, user_id)
    return resolve_rate_cents(patient.rate_cents, appointment_type)


def _resolve_amount_cents(
    payload: CreateChargeRequest,
    patient: Patient,
    user_id: str,
    appointments: AppointmentRepository,
    appointment_types: AppointmentTypeRepository,
) -> int:
    """What to charge: the caller's amount, else the client's effective rate.

    422 when neither an amount nor a rate is available: charging a guessed
    amount, or zero, is worse than refusing.
    """
    if payload.amount_cents is not None:
        return payload.amount_cents

    amount_cents = _effective_rate_cents(
        patient, payload.appointment_id, user_id, appointments, appointment_types
    )
    if amount_cents is None or amount_cents <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No rate is set for this client or appointment type; send an amount to charge.",
        )
    return amount_cents


@router.post("/{patient_id}/payment-method/setup", response_model=CardSetupResponse)
def start_card_setup(
    patient_id: str,
    request: Request,
    user: CurrentUser,
    tenant: Tenant,
    payments: PaymentsRepo,
    patients: PatientsRepo,
    audit: AuditService = Depends(get_audit_service),
) -> CardSetupResponse:
    """Mint the client's Stripe customer (once) and a SetupIntent to collect a card.

    Returns the client secret for Stripe.js, together with the publishable key
    and account it must be initialised with. Nothing chargeable exists yet —
    the payment-method id only arrives once the browser confirms, which is what
    ``POST .../payment-method`` records.

    503 when no publishable key is configured, before anything is created: the
    browser could not collect a card with what we would hand it, and minting a
    Stripe customer and a SetupIntent for a flow that cannot finish leaves
    litter in the practice's Stripe account for nothing.
    """
    credentials = _require_credentials(tenant.practice_id)
    if not credentials.publishable_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Card payments are not configured.",
        )
    _require_patient(patients, patient_id, user.id)

    card = payments.get_card_on_file(patient_id)
    if card is None:
        customer = stripe_request(
            "POST",
            "/v1/customers",
            secret_key=credentials.secret_key,
            account_id=credentials.account_id,
            data={"metadata[pablo_patient_id]": patient_id},
            # Keyed on the client, not random: a double-click must not mint two
            # Stripe customers for the same person.
            idempotency_key=f"patient-customer-create:{patient_id}",
        )
        card = payments.start_card_setup(
            patient_id=patient_id,
            stripe_customer_id=str(customer["id"]),
            user_id=user.id,
        )

    intent = stripe_request(
        "POST",
        "/v1/setup_intents",
        secret_key=credentials.secret_key,
        account_id=credentials.account_id,
        data={
            "customer": card.stripe_customer_id,
            # off_session, because the saved card is charged later with nobody
            # at the keyboard — that is what card-on-file means.
            "usage": "off_session",
            # No metadata: a SetupIntent is single-use scaffolding and its
            # customer already carries the client id. Repeating it would hand
            # Stripe the same identifier twice for nothing.
        },
        # Deliberately not idempotency-keyed: SetupIntents are single-use, so
        # every "add a card" click should get a fresh one.
    )

    audit.log(
        AuditAction.PATIENT_PAYMENT_SETUP_STARTED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
    )
    # No client id in the log line — the audit row above is where a client
    # identifier belongs.
    logger.info("patient_payment_setup_started")
    return CardSetupResponse(
        client_secret=str(intent["client_secret"]),
        publishable_key=credentials.publishable_key,
        stripe_account_id=credentials.account_id,
    )


@router.post("/{patient_id}/payment-method", response_model=CardOnFileResponse)
def complete_card_setup(
    patient_id: str,
    payload: CardSetupConfirmation,
    request: Request,
    user: CurrentUser,
    tenant: Tenant,
    payments: PaymentsRepo,
    patients: PatientsRepo,
    audit: AuditService = Depends(get_audit_service),
) -> CardOnFileResponse:
    """Record the card the browser just confirmed, as Stripe reports it.

    The browser sends only the SetupIntent id; the payment-method id, brand,
    last four and expiry are read back from Stripe. Trusting the browser for
    those would let a caller write display fields that do not match the card
    that will actually be charged.

    409 when the SetupIntent has not succeeded — there is nothing to store yet.
    """
    credentials = _require_credentials(tenant.practice_id)
    _require_patient(patients, patient_id, user.id)

    card = payments.get_card_on_file(patient_id)
    if card is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Card setup was not started for this client.",
        )

    intent = stripe_request(
        "GET",
        f"/v1/setup_intents/{payload.setup_intent_id}",
        secret_key=credentials.secret_key,
        account_id=credentials.account_id,
    )
    # The SetupIntent must belong to THIS client's customer. Without this a
    # caller could hand over a SetupIntent id from someone else's setup and
    # attach that card to this client's row.
    if intent.get("customer") != card.stripe_customer_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setup intent not found.")
    payment_method_id = intent.get("payment_method")
    if intent.get("status") != "succeeded" or not payment_method_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Card setup is not complete."
        )

    method = stripe_request(
        "GET",
        f"/v1/payment_methods/{payment_method_id}",
        secret_key=credentials.secret_key,
        account_id=credentials.account_id,
    )
    stored_card = method.get("card") or {}

    updated = payments.complete_card_setup(
        patient_id=patient_id,
        stripe_payment_method_id=str(payment_method_id),
        brand=stored_card.get("brand"),
        last4=stored_card.get("last4"),
        exp_month=stored_card.get("exp_month"),
        exp_year=stored_card.get("exp_year"),
        user_id=user.id,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Card setup was not started for this client.",
        )

    audit.log(
        AuditAction.PATIENT_PAYMENT_METHOD_STORED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
    )
    logger.info("patient_payment_method_stored brand=%s", updated.card_brand)
    return _to_card_response(updated)


@router.get("/{patient_id}/payment-method", response_model=CardOnFileResponse)
def get_card_on_file(
    patient_id: str,
    request: Request,
    user: CurrentUser,
    tenant: Tenant,
    payments: PaymentsRepo,
    patients: PatientsRepo,
    audit: AuditService = Depends(get_audit_service),
) -> CardOnFileResponse:
    """What card is on file, so the charge button knows whether to enable itself.

    404 when there is none, matching the unknown-client shape: the caller asks
    "is there a card", and "no" is an absent resource rather than an empty
    object every caller would have to special-case.
    """
    _require_credentials(tenant.practice_id)
    _require_patient(patients, patient_id, user.id)

    card = payments.get_card_on_file(patient_id)
    if card is None or not card.chargeable:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No card on file.")

    audit.log(
        AuditAction.PATIENT_PAYMENT_METHOD_VIEWED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
    )
    return _to_card_response(card)


@router.get("/{patient_id}/charge-amount", response_model=ChargeAmountResponse)
def get_charge_amount(
    patient_id: str,
    request: Request,
    user: CurrentUser,
    tenant: Tenant,
    patients: PatientsRepo,
    appointments: AppointmentsRepo,
    appointment_types: AppointmentTypesRepo,
    audit: AuditService = Depends(get_audit_service),
    appointment_id: str | None = None,
) -> ChargeAmountResponse:
    """What a charge with no explicit amount would come to, before it is made.

    The clinician has to see the figure before authorising it, and the amount
    is resolved on this side — so without this the only way to learn it would
    be to charge the card and read the receipt.

    ``amount_cents`` is ``None`` when no rate is set anywhere, which is the
    same condition the charge route refuses on; the UI asks for an amount
    rather than offering a one-click charge for a number nobody chose.
    """
    _require_credentials(tenant.practice_id)
    patient = _require_patient(patients, patient_id, user.id)

    amount_cents = _effective_rate_cents(
        patient, appointment_id, user.id, appointments, appointment_types
    )

    audit.log(
        AuditAction.PATIENT_CHARGE_AMOUNT_VIEWED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
    )
    return ChargeAmountResponse(amount_cents=amount_cents, currency=DEFAULT_CHARGE_CURRENCY)


@router.post("/{patient_id}/charges", response_model=ChargeResponse)
def create_charge(
    patient_id: str,
    payload: CreateChargeRequest,
    request: Request,
    user: CurrentUser,
    tenant: Tenant,
    payments: PaymentsRepo,
    patients: PatientsRepo,
    appointments: AppointmentsRepo,
    appointment_types: AppointmentTypesRepo,
    audit: AuditService = Depends(get_audit_service),
) -> ChargeResponse:
    """Charge the card on file — one click, one charge, no automatic retry.

    Ledger row first (``pending``, committed), then Stripe, then the row is
    updated from the outcome. See the module docstring for why that ordering is
    load-bearing.

    On a decline the row lands ``failed`` with the decline code in
    ``status_detail`` and this returns **200 with that row**, not an error: the
    attempt succeeded as an operation, it is the card that said no, and the
    clinician needs the ledger row and the reason rather than an exception that
    discards both.
    """
    credentials = _require_credentials(tenant.practice_id)
    patient = _require_patient(patients, patient_id, user.id)

    card = payments.get_card_on_file(patient_id)
    if card is None or not card.chargeable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="No card on file for this client."
        )

    amount_cents = _resolve_amount_cents(payload, patient, user.id, appointments, appointment_types)

    # Audited HERE, not after Stripe answers. The § 164.312(b) event is "this
    # clinician initiated a charge against this client", and that is true the
    # moment the ledger row exists. Recording it only on a successful outcome
    # would lose the audit trail for exactly the attempts someone later asks
    # about — a decline, or a call that never came back. The OUTCOME lives on
    # the ledger row this event names. Both are committed together, before
    # Stripe is called at all, so a failure below can roll neither away.
    charge = payments.stage_charge(
        patient_id=patient_id,
        appointment_id=payload.appointment_id,
        amount_cents=amount_cents,
        currency=DEFAULT_CHARGE_CURRENCY,
        user_id=user.id,
    )
    audit.log(
        AuditAction.PATIENT_CHARGE_CREATED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
        changes={"charge_id": charge.id},
    )
    payments.commit()

    # STEP 1 — create the PaymentIntent UNCONFIRMED. No money moves yet.
    create_data: dict[str, Any] = {
        "amount": charge.amount_cents,
        "currency": charge.currency,
        "customer": card.stripe_customer_id,
        "payment_method": card.stripe_payment_method_id,
        # off_session: the client is not at the keyboard when this is confirmed.
        "off_session": "true",
        # Opaque ids only. Stripe copies PaymentIntent metadata onto the charge
        # it creates, so every event type the webhook handles carries these
        # back — which is what lets it tell this application's charges from the
        # ones the practice raises in its own Stripe dashboard. The charge id
        # names the ledger row (and is the breadcrumb a human follows from the
        # dashboard); the user id is the clinician the webhook arms the row
        # policy with and then verifies against the row it actually updated;
        # the practice id is how the webhook finds the right schema.
        f"metadata[{METADATA_CHARGE_ID}]": charge.id,
        f"metadata[{METADATA_USER_ID}]": user.id,
    }
    if tenant.practice_id is not None:
        create_data[f"metadata[{METADATA_PRACTICE_ID}]"] = tenant.practice_id

    _, body = payment_intent_request(
        "/v1/payment_intents",
        secret_key=credentials.secret_key,
        account_id=credentials.account_id,
        data=create_data,
        idempotency_key=f"patient-charge-create:{charge.id}",
    )
    # A bare create cannot be declined — no charge is attempted — so anything
    # other than success was already raised as a 502 by the helper.
    payment_intent_id = str(body.get("id") or "")
    if not payment_intent_id:
        logger.error("payment_intent_create_no_id charge_id=%s", charge.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The card processor returned no payment intent; the charge was not completed.",
        )

    # STEP 2 — write down what is about to be confirmed, and commit, before a
    # cent can move. This is the link a human (or the webhook) reconciles
    # against if the confirm below never comes back.
    payments.record_payment_intent(charge.id, payment_intent_id)

    # STEP 3 — confirm. This is where the card is actually charged, and where a
    # decline comes back.
    accepted, body = payment_intent_request(
        f"/v1/payment_intents/{payment_intent_id}/confirm",
        secret_key=credentials.secret_key,
        account_id=credentials.account_id,
        data={},
        idempotency_key=f"patient-charge-confirm:{charge.id}",
    )

    if accepted:
        intent = body
        detail = None
    else:
        # The decline envelope. The PaymentIntent still exists, and carries the
        # id already recorded in step 2.
        error = body.get("error") or {}
        intent = error.get("payment_intent") or {}
        detail = error.get("decline_code") or error.get("code")

    intent_status = intent.get("status")
    if accepted and intent_status == "succeeded":
        final_status, final_detail = "succeeded", None
    else:
        # Fall back to the intent's own status when the processor gave no code
        # — an off-session card that wants a challenge comes back
        # ``requires_action`` with nothing else. Never invent copy here; the UI
        # maps the token.
        final_status, final_detail = "failed", (detail or intent_status)

    settled = payments.close_charge(charge.id, status=final_status, status_detail=final_detail)

    # Opaque ledger id and amount only — no client id here; the audit row above
    # carries that linkage.
    logger.info(
        "patient_charge_attempted charge_id=%s amount_cents=%d status=%s detail=%s",
        settled.id,
        settled.amount_cents,
        settled.status,
        settled.status_detail,
    )
    return _to_charge_response(settled)


@router.get("/{patient_id}/charges", response_model=list[ChargeResponse])
def list_charges(
    patient_id: str,
    request: Request,
    user: CurrentUser,
    tenant: Tenant,
    payments: PaymentsRepo,
    patients: PatientsRepo,
    audit: AuditService = Depends(get_audit_service),
) -> list[ChargeResponse]:
    """This client's charge ledger, newest first."""
    _require_credentials(tenant.practice_id)
    _require_patient(patients, patient_id, user.id)

    audit.log(
        AuditAction.PATIENT_CHARGES_VIEWED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
    )
    return [_to_charge_response(charge) for charge in payments.list_charges(patient_id)]
