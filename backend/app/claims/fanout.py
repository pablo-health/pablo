# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Running the claims pipeline across a practice, clinician by clinician.

Off-request work has no session and no principal, and the claims tables
are row-policied: a session sees a clinician's claims only when it is
armed as that clinician. So the pipeline fans out twice — over the active
practices, then over each practice's clinicians — and opens one
tenant-scoped session per clinician (:func:`app.db.tenant_session.tenant_db_session`),
handling only the claims that clinician owns. The account values every
837P needs are read once per practice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select

from ..db import create_standalone_session, get_engine
from ..db.migrate_tenants import list_active_practice_registry
from ..db.models import PracticeBillingProfileRow
from ..db.platform_models import EmailTenantMappingRow, PlatformUserRow
from ..db.tenant_session import tenant_db_session
from ..repositories.postgres.claim_receipts import PostgresClaimReceiptRepository
from ..repositories.postgres.claims import PostgresClaimRepository
from ..repositories.postgres.coverage import PostgresPayerRepository
from ..repositories.postgres.patient_payment import PostgresPatientPaymentRepository
from ..repositories.postgres.remittance_hold import PostgresRemittanceHoldRepository
from ..services.practice_billing_profile import SINGLETON_ID
from ..services.token_encryption import decrypt_tokens
from .acknowledgments import FetchedAcknowledgment, apply_fetched, fetch_acknowledgment
from .clearinghouse import ClearinghouseNotFoundError
from .credentials import get_clearinghouse_credential_provider
from .enrollment import clearinghouse_client_for_practice
from .receipts import ClaimPipeline
from .remittance import apply_remittance
from .remittance_feed import FetchedRemittance, fetch_remittance
from .routing import route_for_control_numbers
from .stedi import RECEIVER_NAME, SUBMITTER_IDENTIFICATION
from .submit_worker import SubmissionAccount

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.orm import Session

    from ..repositories.coverage import PayerRepository
    from ..repositories.patient_payment import PatientPaymentRepository
    from .clearinghouse import ClearinghouseClient
    from .routing import ClaimRoute
    from .webhooks import WebhookEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PracticeContext:
    """One practice the pipeline can run in: its schema, its account, its people."""

    schema: str
    practice_id: str | None
    client: ClearinghouseClient
    user_ids: list[str]


@dataclass
class TenantRun:
    """What one clinician's unit of work is handed."""

    pipeline: ClaimPipeline
    payers: PayerRepository
    commit: Callable[[], None]
    #: The client charge ledger, so what a payer says a client owes reaches
    #: the client rather than stopping at the claim. Bound to the same
    #: tenant session as everything else in the run.
    charges: PatientPaymentRepository | None = None


def practice_user_ids(practice_id: str | None) -> list[str]:
    """Every clinician of the practice, by the platform's email-to-practice map.

    A deployment with no mapping rows (one practice, everybody in it)
    falls back to every platform user.
    """
    with create_standalone_session() as db:
        mapped = (
            db.execute(
                select(PlatformUserRow.id)
                .join(EmailTenantMappingRow, EmailTenantMappingRow.email == PlatformUserRow.email)
                .where(EmailTenantMappingRow.practice_id == (practice_id or ""))
            )
            .scalars()
            .all()
        )
        if mapped:
            return list(mapped)
        return list(db.execute(select(PlatformUserRow.id)).scalars().all())


def active_practices(*, max_tenants: int) -> Iterator[PracticeContext]:
    """The practices with a clearinghouse configured, in schema order."""
    for schema, practice_id in list_active_practice_registry(get_engine())[:max_tenants]:
        client = clearinghouse_client_for_practice(practice_id)
        if client is None:
            continue
        yield PracticeContext(
            schema=schema,
            practice_id=practice_id,
            client=client,
            user_ids=practice_user_ids(practice_id),
        )


def load_submission_account(session: Session, practice_id: str | None) -> SubmissionAccount | None:
    """The per-account 837P values for the practice, or ``None`` if it cannot file yet.

    The tax id is decrypted here and handed to the wire mapping, nowhere
    else; the usage indicator follows the key's mode so a test key can
    never file a production claim.
    """
    profile = session.get(PracticeBillingProfileRow, SINGLETON_ID)
    if profile is None or not profile.tax_id_encrypted:
        return None
    credentials = get_clearinghouse_credential_provider().get(practice_id)
    usage: Literal["T", "P"] = (
        "T" if credentials is not None and credentials.mode == "test" else "P"
    )
    return SubmissionAccount(
        usage_indicator=usage,
        tax_id=decrypt_tokens(profile.tax_id_encrypted)["tax_id"],
        submitter_identification=SUBMITTER_IDENTIFICATION,
        receiver_name=RECEIVER_NAME,
    )


WebhookOutcome = Literal[
    "moved",
    "recorded",
    "duplicate",
    "not_applicable",
    "unmatched",
    "ignored",
]


def _routing_client() -> ClearinghouseClient | None:
    """THE clearinghouse account, or ``None`` if the deployment has not configured one.

    Reading the document is the only way to learn the claim control number it
    names, and the control number is what the index is keyed on. One read, one
    account: the credential provider resolves the deployment's own key and
    ignores which practice is asking (see
    :class:`app.claims.credentials.SettingsClearinghouseCredentialProvider`) —
    the same fact the single webhook endpoint with its single signing secret
    states from the other side.

    ``None`` is passed deliberately; the protocol defines it as "no particular
    practice". Asking practices one at a time for an account they all share
    would be the fan-out this change removed, wearing a different hat.
    """
    return clearinghouse_client_for_practice(None)


def _control_numbers_of(fetched: FetchedAcknowledgment | FetchedRemittance) -> set[str]:
    """The claim control numbers the document names."""
    if isinstance(fetched, FetchedAcknowledgment):
        return {number.upper() for number in fetched.control_numbers if number}
    return {
        detail.patient_control_number.upper()
        for remittance in fetched.remittances
        for detail in remittance.claims
        if detail.patient_control_number
    }


def _read_document(
    transaction_id: str,
) -> FetchedAcknowledgment | FetchedRemittance | WebhookOutcome | None:
    """The inbound document, ``"ignored"`` if we can read it and have no use for
    it, or ``None`` if our account cannot read it at all.

    A vendor outage is deliberately NOT caught: the receiver must answer 503 so
    the vendor redelivers, and swallowing it would turn an outage into a silent
    ``unmatched`` — the exact failure this change is about.
    """
    client = _routing_client()
    if client is None:
        return None
    try:
        fetched: FetchedAcknowledgment | FetchedRemittance | None = fetch_acknowledgment(
            client, transaction_id
        )
        if fetched is None:
            fetched = fetch_remittance(client, transaction_id)
    except ClearinghouseNotFoundError:
        return None
    return "ignored" if fetched is None else fetched


def ingest_transaction_event(event: WebhookEvent) -> WebhookOutcome:
    """Apply one ``transaction.processed`` delivery to the claim that owns it.

    Read the document once, look the control number up in
    ``platform.claim_routes``, open that one tenant session as that one
    clinician. A 277CA moves the claim through the acknowledgment it carries;
    an 835 posts the remittance immediately rather than waiting for the
    pipeline's next pass — apply_posting is idempotent on the vendor entry id,
    so that pass posts nothing twice. Any other inbound document is
    ``ignored``. Vendor outages propagate so the receiver can ask for a
    redelivery.

    There is no search left in this path. It used to open every practice in
    turn and, inside each, a session per clinician, asking whether anyone could
    see the claim. The outer loop had to be bounded — a webhook cannot visit an
    unbounded registry inside the vendor's response timeout — so a practice
    past the bound was never asked at all: the delivery answered ``unmatched``,
    which is also what a delivery for somebody else's claim answers, so nothing
    alerted while the claim silently stopped moving. Measured on dev, where the
    practice holding the claims ranked 70th of 78 against a bound of 50
    (PABLO-ffw8).

    A control number with no row answers ``unmatched``. Nothing stands behind
    the lookup: the claim still moves on the pipeline's next polling pass,
    which is where a claim filed before the index existed is collected.
    """
    transaction_id = event.transaction_id
    if transaction_id is None:
        return "ignored"
    fetched = _read_document(transaction_id)
    if fetched is None:
        return "unmatched"
    if isinstance(fetched, str):
        return fetched
    route = route_for_control_numbers(_control_numbers_of(fetched))
    if route is None:
        return "unmatched"
    applied = (
        _apply_acknowledgment(route, fetched, event.id)
        if isinstance(fetched, FetchedAcknowledgment)
        else _apply_remittance(route, fetched)
    )
    if applied is None:
        # The index named a claim this clinician cannot see. Loud: the index is
        # meant to BE the answer, so a miss is a bug in what filing recorded.
        logger.warning(
            "claim_route_stale practice_id=%s user_id=%s transaction_id=%s",
            route.practice_id,
            route.user_id,
            transaction_id,
        )
        return "unmatched"
    return applied


def _pipeline_for(route: ClaimRoute, session: Session) -> ClaimPipeline:
    return ClaimPipeline(
        claims=PostgresClaimRepository(session),
        receipts=PostgresClaimReceiptRepository(session),
        session=session,
        principal_user_id=route.user_id,
        holds=PostgresRemittanceHoldRepository(session),
    )


def _apply_acknowledgment(
    route: ClaimRoute, fetched: FetchedAcknowledgment, event_id: str
) -> WebhookOutcome | None:
    with tenant_db_session(route.schema, route.user_id) as session:
        outcomes = [
            outcome
            for outcome, _claim in apply_fetched(
                _pipeline_for(route, session), fetched, vendor_event_id=event_id
            )
        ]
    for wanted in ("moved", "recorded", "duplicate"):
        if wanted in outcomes:
            return wanted
    return None


def _apply_remittance(route: ClaimRoute, fetched: FetchedRemittance) -> WebhookOutcome | None:
    with tenant_db_session(route.schema, route.user_id) as session:
        pipeline = _pipeline_for(route, session)
        outcomes = [
            apply_remittance(
                pipeline,
                detail,
                transaction_id=fetched.transaction_id,
                occurred_at=fetched.processed_at,
            )[0]
            for remittance in fetched.remittances
            for detail in remittance.claims
        ]
    for wanted in ("moved", "duplicate", "not_applicable"):
        if wanted in outcomes:
            return wanted
    return None


def for_each_clinician(practice: PracticeContext, work: Callable[[TenantRun, str], None]) -> int:
    """Run ``work`` once per clinician of the practice, each in their own session.

    One clinician's failure is logged and the next still runs. Returns how
    many sessions completed.
    """
    completed = 0
    for user_id in practice.user_ids:
        try:
            with tenant_db_session(practice.schema, user_id) as session:
                run = TenantRun(
                    pipeline=ClaimPipeline(
                        claims=PostgresClaimRepository(session),
                        receipts=PostgresClaimReceiptRepository(session),
                        session=session,
                        principal_user_id=user_id,
                        holds=PostgresRemittanceHoldRepository(session),
                    ),
                    payers=PostgresPayerRepository(session),
                    commit=session.commit,
                    charges=PostgresPatientPaymentRepository(session),
                )
                work(run, user_id)
            completed += 1
        except Exception:  # one clinician's failure must not stop the next
            logger.exception(
                "claims_pipeline_clinician_failed schema=%s user_id=%s", practice.schema, user_id
            )
    return completed
