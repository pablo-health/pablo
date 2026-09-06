# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Enrolling the practice with payers through its clearinghouse account.

Before a payer accepts a practice's claims or returns remittances
electronically, the practice has to be enrolled with that payer: one
request per transaction type, filed through the clearinghouse, and answered
by the payer on its own schedule. Remittance (835) always needs one; claims
(837P) and eligibility (270) only when the payer's directory entry says so.

Three things live here:

* :func:`ensure_provider_record` — the practice's one provider record at the
  clearinghouse, created from the billing profile the first time it is
  complete. Its contact is the practice's general inbox, never a clinician.
* :func:`request_enrollments` — files whatever a payer needs that has not
  been filed yet, records each request on ``payer_enrollments``, and mirrors
  the set into ``payers.enrollment_status``.
* :func:`refresh_enrollments` — polls the clearinghouse for every open
  request and records what changed. Bounded, and run per tenant by the
  daily job in ``app.jobs.payer_enrollment_refresh``.

A request that lands in ``provider_action_required`` is something a person
must do — sign a form, attest, upload a document. That is raised as an
``enrollment_action_required`` claim event through :mod:`app.claims.events`
with the clearinghouse's own instructions; the default listener turns it
into a compliance reminder, and this module resolves that reminder when the
request moves on.

The clearinghouse client is resolved through a small factory registry so a
deployment can swap the adapter and tests can hand in a fake; the vendor's
enrollment API refuses test-mode keys, so the recorded fixtures are the only
way the lifecycle is exercised outside production.

What is logged: payer ids, transaction types, vendor request ids and
statuses. The instructions text is stored and shown, never logged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..db import arm_current_user_id
from ..db.models import (
    ENROLLMENT_TRANSACTION_TYPES,
    PAYER_ENROLLMENT_REQUEST_STATUSES,
    PayerEnrollmentRow,
    PayerRow,
    PracticeBillingProfileRow,
)
from ..models.claims_transport import (
    EnrollmentFilters,
    EnrollmentPayerRef,
    EnrollmentProviderRef,
    EnrollmentRequest,
    EnrollmentTransactions,
    ProviderContact,
    ProviderRegistration,
    TransactionEnrollment,
)
from ..services.coverage_intake import UNKNOWN_PAYER_ID
from ..services.practice_billing_profile import SINGLETON_ID
from ..services.token_encryption import decrypt_tokens
from ..utcnow import utc_now
from .clearinghouse import ClearinghouseClient, ClearinghouseError
from .credentials import get_clearinghouse_credential_provider
from .events import ClaimEvent, ClaimEventDetail, emit, resolve_compliance_reminder

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

    from ..models.claims_transport import Enrollment, Payer

logger = logging.getLogger(__name__)

#: The vendor's key for each transaction type, in its ``transactions`` object
#: and its payer directory's ``transactionSupport``.
_TRANSACTION_KEYS: dict[str, str] = {
    "837P": "professionalClaimSubmission",
    "270": "eligibilityCheck",
    "835": "claimPayment",
}

#: Statuses a request can still move out of on its own; the refresh polls
#: these and leaves ``live`` / ``rejected`` / ``canceled`` alone.
OPEN_REQUEST_STATUSES: frozenset[str] = frozenset(
    {"draft", "stedi_action_required", "provider_action_required", "provisioning"}
)

#: How many open requests one refresh pass looks at in one tenant. A
#: practice has a handful of payers and at most three requests each; the
#: cap is what keeps a runaway tenant from monopolising the daily job.
MAX_REFRESH_PER_TENANT = 200

#: The vendor's directory answer that means "file an enrollment first".
_ENROLLMENT_REQUIRED = "ENROLLMENT_REQUIRED"


class BillingProfileIncompleteError(Exception):
    """The billing profile lacks something the clearinghouse needs on the provider record."""

    def __init__(self, missing: tuple[str, ...]) -> None:
        super().__init__(f"Billing profile is missing: {', '.join(missing)}.")
        self.missing = missing


class PayerNotInDirectoryError(Exception):
    """The payer's electronic id matched nothing in the clearinghouse's directory."""

    def __init__(self, payer_id: str) -> None:
        super().__init__(f"Payer id {payer_id!r} is not in the clearinghouse's payer directory.")
        self.payer_id = payer_id


# --- The clearinghouse client seam -------------------------------------------

ClearinghouseClientFactory = Callable[[str | None], ClearinghouseClient | None]
"""Resolves a practice to the client its enrollment calls are made with, or
``None`` when the practice has no clearinghouse configured."""


def _default_client_factory(practice_id: str | None) -> ClearinghouseClient | None:
    credentials = get_clearinghouse_credential_provider().get(practice_id)
    if credentials is None:
        return None
    from .stedi import StediClearinghouseClient  # noqa: PLC0415 — httpx only when needed

    return StediClearinghouseClient(credentials)


@dataclass
class _FactoryRegistry:
    factory: ClearinghouseClientFactory | None = None


_registry = _FactoryRegistry()


def register_clearinghouse_client_factory(factory: ClearinghouseClientFactory | None) -> None:
    """Install the process-global client factory, or ``None`` to restore the default.

    Same shape as ``register_clearinghouse_credential_provider``: called once
    at startup by a deployment that wires its own adapter, and by tests to
    hand in a fake that answers from recorded fixtures.
    """
    _registry.factory = factory


def get_clearinghouse_client(practice_id: str | None) -> ClearinghouseClient | None:
    """The client for ``practice_id``, or ``None`` when none is configured."""
    return (_registry.factory or _default_client_factory)(practice_id)


# --- The provider record ---------------------------------------------------------

#: What the clearinghouse needs on a provider record and its contact. The
#: tax id is checked through its encrypted column; ``address_line2`` is the
#: one address field that may be blank.
_PROVIDER_RECORD_FIELDS: tuple[str, ...] = (
    "legal_name",
    "tax_id_encrypted",
    "tax_id_type",
    "billing_npi",
    "address_line1",
    "city",
    "state",
    "postal_code",
    "phone",
    "contact_email",
)


def billing_profile_missing_fields(profile: PracticeBillingProfileRow | None) -> tuple[str, ...]:
    """The billing-profile fields still empty that the provider record needs.

    Named as the API knows them (``tax_id``, not the encrypted column), so
    the answer can be shown to the person filling the form in.
    """
    if profile is None:
        return tuple("tax_id" if f == "tax_id_encrypted" else f for f in _PROVIDER_RECORD_FIELDS)
    return tuple(
        "tax_id" if field == "tax_id_encrypted" else field
        for field in _PROVIDER_RECORD_FIELDS
        if not getattr(profile, field)
    )


def _contact(profile: PracticeBillingProfileRow) -> ProviderContact:
    return ProviderContact(
        organizationName=profile.legal_name or "",
        email=profile.contact_email or "",
        phone=profile.phone or "",
        streetAddress1=profile.address_line1 or "",
        city=profile.city or "",
        zipCode=profile.postal_code or "",
        state=profile.state or "",
    )


def _registration(profile: PracticeBillingProfileRow) -> ProviderRegistration:
    tax_id = decrypt_tokens(profile.tax_id_encrypted or "")["tax_id"]
    return ProviderRegistration(
        name=profile.legal_name or "",
        npi=profile.billing_npi or "",
        taxId="".join(ch for ch in tax_id if ch.isdigit()),
        taxIdType="SSN" if profile.tax_id_type == "ssn" else "EIN",
        contacts=[_contact(profile)],
    )


def ensure_provider_record(session: Session, client: ClearinghouseClient) -> str:
    """The clearinghouse's id for this practice's provider record, creating it once.

    Raises :class:`BillingProfileIncompleteError` when the profile cannot
    yet be registered; the caller decides whether that is a quiet skip (a
    profile save) or an answer to a person (an enrollment they asked for).
    Does not commit — the caller owns the transaction.
    """
    profile = session.get(PracticeBillingProfileRow, SINGLETON_ID)
    if profile is not None and profile.clearinghouse_provider_id:
        return profile.clearinghouse_provider_id
    missing = billing_profile_missing_fields(profile)
    if missing or profile is None:
        raise BillingProfileIncompleteError(missing)

    record = client.create_provider(_registration(profile))
    profile.clearinghouse_provider_id = record.id
    profile.updated_at = utc_now()
    session.flush()
    logger.info("clearinghouse_provider_registered provider_id=%s", record.id)
    return record.id


def sync_provider_record(session: Session, practice_id: str | None) -> str | None:
    """Register the provider record when a billing-profile save completes the profile.

    Quiet by design: an incomplete profile, an unconfigured clearinghouse or
    a vendor error leaves the save itself untouched — the record is created
    on the next save that succeeds, or when an enrollment is requested.
    """
    client = get_clearinghouse_client(practice_id)
    if client is None:
        return None
    try:
        return ensure_provider_record(session, client)
    except BillingProfileIncompleteError:
        return None
    except ClearinghouseError as exc:
        logger.warning("clearinghouse_provider_registration_failed error=%s", type(exc).__name__)
        return None


# --- Requests --------------------------------------------------------------------


def list_enrollments(session: Session, payer_row_id: str) -> list[PayerEnrollmentRow]:
    """Every enrollment request on file for one payer, in transaction order."""
    rows = session.execute(
        select(PayerEnrollmentRow).where(PayerEnrollmentRow.payer_id == payer_row_id)
    ).scalars()
    order = {tx: i for i, tx in enumerate(ENROLLMENT_TRANSACTION_TYPES)}
    return sorted(rows, key=lambda row: order[row.transaction_type])


def _directory_entry(client: ClearinghouseClient, payer_id: str) -> Payer | None:
    wanted = payer_id.strip().upper()
    for hit in client.search_payers(payer_id):
        known = {hit.primaryPayerId.upper(), hit.stediId.upper(), *(a.upper() for a in hit.aliases)}
        if wanted in known:
            return hit
    return None


def required_transactions(transaction_support: Mapping[str, str]) -> list[str]:
    """Which transaction types need an enrollment request with this payer.

    Files for a transaction the directory marks ``ENROLLMENT_REQUIRED``, and
    for remittance when the directory says nothing about it — a payer that
    returns remittances at all does so only to enrolled providers. A
    transaction the directory marks ``SUPPORTED`` needs no request and gets
    none; one marked ``NOT_SUPPORTED`` cannot be enrolled for.
    """
    required = []
    for transaction_type in ENROLLMENT_TRANSACTION_TYPES:
        support = transaction_support.get(_TRANSACTION_KEYS[transaction_type])
        if support == _ENROLLMENT_REQUIRED or (support is None and transaction_type == "835"):
            required.append(transaction_type)
    return required


def _request(
    provider_id: str, payer: PayerRow, contact: ProviderContact, transaction_type: str
) -> EnrollmentRequest:
    transactions = EnrollmentTransactions(
        **{_TRANSACTION_KEYS[transaction_type]: TransactionEnrollment(enroll=True)}
    )
    return EnrollmentRequest(
        provider=EnrollmentProviderRef(id=provider_id),
        payer=EnrollmentPayerRef(idOrAlias=payer.clearinghouse_payer_id or payer.payer_id),
        primaryContact=contact,
        transactions=transactions,
        userEmail=contact.email,
        status="STEDI_ACTION_REQUIRED",
    )


def request_enrollments(
    session: Session,
    client: ClearinghouseClient,
    *,
    payer_row_id: str,
    user_id: str,
    now: datetime | None = None,
) -> list[PayerEnrollmentRow]:
    """File every enrollment this payer needs that is not on file yet.

    Returns the requests created by this call; a payer whose requests were
    all filed earlier gets an empty list and no vendor call. ``user_id`` is
    who asked, and who the reminder goes to if the payer wants something.

    Each request is flushed as it is filed, so a vendor error on the second
    request keeps the first — the next call picks up where this one
    stopped. Does not commit.
    """
    now = now or utc_now()
    payer = session.get(PayerRow, payer_row_id)
    if payer is None:
        msg = f"payer {payer_row_id!r} not found"
        raise LookupError(msg)
    if payer.payer_id == UNKNOWN_PAYER_ID:
        raise PayerNotInDirectoryError(payer.payer_id)

    provider_id = ensure_provider_record(session, client)
    profile = session.get(PracticeBillingProfileRow, SINGLETON_ID)
    assert profile is not None  # noqa: S101 — ensure_provider_record just read it
    contact = _contact(profile)

    existing = {row.transaction_type for row in list_enrollments(session, payer_row_id)}
    entry = _directory_entry(client, payer.payer_id)
    if entry is None:
        raise PayerNotInDirectoryError(payer.payer_id)
    if payer.clearinghouse_payer_id != entry.stediId:
        payer.clearinghouse_payer_id = entry.stediId

    created: list[PayerEnrollmentRow] = []
    for transaction_type in required_transactions(entry.transactionSupport):
        if transaction_type in existing:
            continue
        enrollment = client.create_enrollment(
            _request(provider_id, payer, contact, transaction_type)
        )
        row = PayerEnrollmentRow(
            payer_id=payer.id,
            transaction_type=transaction_type,
            vendor_request_id=enrollment.id,
            status=_status(enrollment.status) or "draft",
            instructions=_instructions(enrollment),
            requested_by_user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        logger.info(
            "payer_enrollment_requested payer_id=%s transaction=%s vendor_request_id=%s status=%s",
            payer.payer_id,
            transaction_type,
            row.vendor_request_id,
            row.status,
        )
        if row.status == "provider_action_required":
            _emit_action_required(session, row, payer, now)
        created.append(row)

    _mirror_status(session, payer, now)
    return created


def enroll_if_new(
    session: Session, practice_id: str | None, *, payer_row_id: str, user_id: str
) -> None:
    """The coverage-save trigger: file for a payer with no requests on file yet.

    Never fails the save it rides on. A practice without a clearinghouse, an
    incomplete billing profile, a payer the directory does not know or a
    vendor error each log why and leave the payer for the "Enroll with
    payer" button in Settings.
    """
    if list_enrollments(session, payer_row_id):
        return
    client = get_clearinghouse_client(practice_id)
    if client is None:
        return
    try:
        request_enrollments(session, client, payer_row_id=payer_row_id, user_id=user_id)
    except BillingProfileIncompleteError:
        logger.info("payer_enrollment_skipped_profile_incomplete payer_row_id=%s", payer_row_id)
    except PayerNotInDirectoryError as exc:
        logger.info("payer_enrollment_skipped_unknown_payer payer_id=%s", exc.payer_id)
    except ClearinghouseError as exc:
        logger.warning(
            "payer_enrollment_request_failed payer_row_id=%s error=%s",
            payer_row_id,
            type(exc).__name__,
        )


# --- Status ------------------------------------------------------------------------

#: The vendor's deprecated ``SUBMITTED`` is what ``STEDI_ACTION_REQUIRED`` used to be called.
_LEGACY_STATUSES: dict[str, str] = {"submitted": "stedi_action_required"}


def _status(vendor_status: str) -> str | None:
    """The vendor's status in this table's vocabulary, or ``None`` if unrecognised."""
    status = vendor_status.strip().lower()
    status = _LEGACY_STATUSES.get(status, status)
    return status if status in PAYER_ENROLLMENT_REQUEST_STATUSES else None


def _instructions(enrollment: Enrollment) -> str | None:
    """What the clearinghouse wants the practice to do, as one block of text.

    The open tasks assigned to the provider, each with its links, then the
    vendor's ``reason`` note when there is one.
    """
    parts: list[str] = []
    for task in enrollment.tasks:
        if task.responsibleParty != "PROVIDER" or task.isComplete or task.definition is None:
            continue
        manual = task.definition.manualTask
        if manual is None:
            continue
        if manual.instructions:
            parts.append(manual.instructions.strip())
        parts.extend(f"{link.label}: {link.url}" for link in manual.links)
    if enrollment.reason:
        parts.append(enrollment.reason.strip())
    return "\n".join(parts) or None


def derive_payer_status(statuses: Iterable[tuple[str, str]]) -> str:
    """``payers.enrollment_status`` from the payer's ``(transaction_type, status)`` requests.

    ``active`` means claims can go out and remittances come back: remittance
    is live, and claims are live or never needed a request (a payer whose
    directory entry marks claims ``SUPPORTED`` gets no 837P request, and
    remittance alone is what stands between it and ``active``). A rejection
    anywhere is ``error``; anything the payer or vendor has started on is
    ``pending``; requests filed and not yet picked up are ``filed``.
    """
    by_transaction = dict(statuses)
    if not by_transaction:
        return "none"
    if "rejected" in by_transaction.values():
        return "error"
    claims_ready = by_transaction.get("837P", "live") == "live"
    if claims_ready and by_transaction.get("835") == "live":
        return "active"
    if any(
        s in {"provider_action_required", "provisioning", "live"} for s in by_transaction.values()
    ):
        return "pending"
    return "filed"


def _mirror_status(session: Session, payer: PayerRow, now: datetime) -> None:
    status = derive_payer_status(
        (row.transaction_type, row.status) for row in list_enrollments(session, payer.id)
    )
    if payer.enrollment_status != status:
        payer.enrollment_status = status
        payer.updated_at = now
        session.flush()
        logger.info(
            "payer_enrollment_status_mirrored payer_id=%s status=%s", payer.payer_id, status
        )


def _emit_action_required(
    session: Session, row: PayerEnrollmentRow, payer: PayerRow, now: datetime
) -> None:
    emit(
        session,
        ClaimEvent(
            kind="enrollment_action_required",
            control_number=row.vendor_request_id,
            claim_id=f"{row.payer_id}:{row.transaction_type}",
            user_id=row.requested_by_user_id,
            payer_id=payer.payer_id,
            payer_name=payer.name,
            state=row.status,
            occurred_at=now,
            detail=ClaimEventDetail(payer_instructions=row.instructions),
        ),
    )


def apply_vendor_status(
    session: Session,
    row: PayerEnrollmentRow,
    enrollment: Enrollment,
    *,
    payer: PayerRow,
    now: datetime | None = None,
) -> bool:
    """Record what the clearinghouse now says about one request.

    Returns whether anything changed. Moving into
    ``provider_action_required`` raises the claim event; moving out of it
    resolves the reminder that event wrote. The session must be armed as
    ``row.requested_by_user_id`` — the reminder is that person's row.
    """
    now = now or utc_now()
    status = _status(enrollment.status)
    if status is None:
        logger.warning(
            "payer_enrollment_status_unrecognised vendor_request_id=%s status=%s",
            row.vendor_request_id,
            enrollment.status,
        )
        return False
    instructions = _instructions(enrollment)
    if status == row.status and instructions == row.instructions:
        return False

    previous = row.status
    row.status = status
    row.instructions = instructions
    row.updated_at = now
    session.flush()
    logger.info(
        "payer_enrollment_status payer_id=%s transaction=%s vendor_request_id=%s status=%s",
        payer.payer_id,
        row.transaction_type,
        row.vendor_request_id,
        status,
    )
    if status == "provider_action_required" and previous != status:
        _emit_action_required(session, row, payer, now)
    elif previous == "provider_action_required" and status != previous:
        resolve_compliance_reminder(
            session,
            kind="enrollment_action_required",
            control_number=row.vendor_request_id,
            user_id=row.requested_by_user_id,
        )
    return True


PrincipalArmer = Callable[["Session", str], None]
"""How :func:`refresh_enrollments` arms the session as each request's owner
before it writes that person's reminder. The default is the real RLS arm;
a test on a database without the GUC hands in a no-op."""


def refresh_enrollments(
    session: Session,
    client: ClearinghouseClient,
    *,
    limit: int = MAX_REFRESH_PER_TENANT,
    arm: PrincipalArmer = arm_current_user_id,
    now: datetime | None = None,
) -> int:
    """Poll the clearinghouse for this tenant's open requests; returns how many changed.

    One listing call per pass, then at most ``limit`` requests matched by
    vendor id. Requests the listing does not mention — the vendor's first
    page only, or one it no longer knows — are left as they are. The
    session is re-armed per request owner so the reminder a status change
    writes lands under that person's row policy. Does not commit.
    """
    now = now or utc_now()
    rows = (
        session.execute(
            select(PayerEnrollmentRow)
            .where(PayerEnrollmentRow.status.in_(OPEN_REQUEST_STATUSES))
            .order_by(
                PayerEnrollmentRow.requested_by_user_id,
                PayerEnrollmentRow.payer_id,
                PayerEnrollmentRow.transaction_type,
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0

    by_vendor_id = {e.id: e for e in client.list_enrollments(EnrollmentFilters())}
    changed = 0
    touched: dict[str, PayerRow] = {}
    for row in rows:
        enrollment = by_vendor_id.get(row.vendor_request_id)
        if enrollment is None:
            continue
        payer = session.get(PayerRow, row.payer_id)
        if payer is None:
            continue
        arm(session, row.requested_by_user_id)
        if apply_vendor_status(session, row, enrollment, payer=payer, now=now):
            changed += 1
            touched[payer.id] = payer
    for payer in touched.values():
        _mirror_status(session, payer, now)
    return changed
