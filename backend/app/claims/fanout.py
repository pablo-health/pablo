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
from ..services.practice_billing_profile import SINGLETON_ID
from ..services.token_encryption import decrypt_tokens
from .acknowledgments import FetchedAcknowledgment, apply_fetched, fetch_acknowledgment
from .clearinghouse import ClearinghouseNotFoundError
from .credentials import get_clearinghouse_credential_provider
from .enrollment import clearinghouse_client_for_practice
from .receipts import ClaimPipeline
from .remittance import apply_remittance
from .remittance_feed import FetchedRemittance, fetch_remittance
from .routing import practice_for_control_numbers
from .stedi import RECEIVER_NAME, SUBMITTER_IDENTIFICATION
from .submit_worker import SubmissionAccount

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.orm import Session

    from ..repositories.coverage import PayerRepository
    from .clearinghouse import ClearinghouseClient
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
    """One account to read the inbound document through.

    Reading it is the only way to learn the claim control number it names, and
    the control number is what the index is keyed on. One clearinghouse account
    serves every practice — which is what the single webhook endpoint, with its
    single signing secret, already tells you — so one read is all there is.

    Walking the registry for the first practice that HAS credentials is not the
    fan-out this replaced: it opens no tenant session and makes no vendor call,
    it just finds an account to read through. A practice that is provisioned
    but not yet enrolled answers ``None`` and is skipped.
    """
    try:
        for _schema, practice_id in list_active_practice_registry(get_engine()):
            client = clearinghouse_client_for_practice(practice_id)
            if client is not None:
                return client
    except Exception:
        logger.exception("claim_route_registry_unavailable")
    return None


def _practice_context(practice_id: str) -> PracticeContext | None:
    """Open the one practice the index named."""
    try:
        for schema, candidate in list_active_practice_registry(get_engine()):
            if candidate != practice_id:
                continue
            client = clearinghouse_client_for_practice(candidate)
            if client is None:
                return None
            return PracticeContext(
                schema=schema,
                practice_id=candidate,
                client=client,
                user_ids=practice_user_ids(candidate),
            )
    except Exception:
        logger.exception("claim_route_practice_unavailable practice_id=%s", practice_id)
    return None


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


def _route(
    transaction_id: str,
) -> tuple[PracticeContext, FetchedAcknowledgment | FetchedRemittance] | WebhookOutcome:
    """The practice this document belongs to and the document itself.

    Returns the outcome instead when there is nobody to hand it to, because the
    reasons are not interchangeable: ``ignored`` is a document we can read and
    have no use for, ``unmatched`` is a document naming a claim that is not
    ours. A vendor outage is deliberately NOT caught — the receiver must answer
    503 so the vendor redelivers, and swallowing it would turn an outage into a
    silent ``unmatched``, which is the exact failure this change is about.
    """
    client = _routing_client()
    if client is None:
        return "unmatched"
    fetched: FetchedAcknowledgment | FetchedRemittance | None
    try:
        fetched = fetch_acknowledgment(client, transaction_id)
        if fetched is None:
            fetched = fetch_remittance(client, transaction_id)
    except ClearinghouseNotFoundError:
        return "unmatched"
    if fetched is None:
        # Ours to read, but neither a 277CA nor an 835.
        return "ignored"
    practice_id = practice_for_control_numbers(_control_numbers_of(fetched))
    if practice_id is None:
        return "unmatched"
    practice = _practice_context(practice_id)
    return "unmatched" if practice is None else (practice, fetched)


def ingest_transaction_event(event: WebhookEvent) -> WebhookOutcome:
    """Apply one ``transaction.processed`` delivery to the practice that owns it.

    Which practice that is, is a LOOKUP. Filing records the claim's control
    number against the practice that filed it (:mod:`app.claims.routing`), so
    the document names its own owner: read it once, ask the index, open that
    one practice. A 277CA moves the claim through the acknowledgment it
    carries; an 835 posts the remittance immediately rather than waiting for
    the pipeline's next pass — apply_posting is idempotent on the vendor entry
    id, so that pass posts nothing twice. Any other inbound document is
    ``ignored``. Vendor outages propagate so the receiver can ask for a
    redelivery.

    This used to be a fan-out: open every practice in turn and ask whether any
    of its clinicians could see the claim. That scan was bounded — it had to
    be, a webhook cannot visit an unbounded registry inside the vendor's
    response timeout — and a practice past the bound was therefore never asked
    at all. The delivery answered ``unmatched``, which is also what a delivery
    for somebody else's claim answers, so nothing alerted while the claim
    silently stopped moving. Measured on dev, where the practice holding the
    claims ranked 70th of 78 against a bound of 50 (PABLO-ffw8).

    No fallback stands behind the lookup. Nothing was billing through this yet,
    so there is no population of unindexed claims to keep a scan alive for.
    """
    transaction_id = event.transaction_id
    if transaction_id is None:
        return "ignored"
    routed = _route(transaction_id)
    if isinstance(routed, str):
        return routed
    practice, fetched = routed
    applied = (
        _apply_in_practice(practice, fetched, event.id)
        if isinstance(fetched, FetchedAcknowledgment)
        else _apply_remittance_in_practice(practice, fetched)
    )
    if applied is None:
        # The index named a practice whose clinicians cannot see the claim.
        # Loud: the index is meant to BE the answer, so a miss here is a bug
        # in what filing recorded, not a routine outcome.
        logger.warning(
            "claim_route_stale practice_id=%s transaction_id=%s",
            practice.practice_id,
            transaction_id,
        )
        return "unmatched"
    return applied


def _apply_in_practice(
    practice: PracticeContext, fetched: FetchedAcknowledgment, event_id: str
) -> WebhookOutcome | None:
    for user_id in practice.user_ids:
        with tenant_db_session(practice.schema, user_id) as session:
            pipeline = ClaimPipeline(
                claims=PostgresClaimRepository(session),
                receipts=PostgresClaimReceiptRepository(session),
                session=session,
                principal_user_id=user_id,
            )
            outcomes = [
                outcome
                for outcome, _claim in apply_fetched(pipeline, fetched, vendor_event_id=event_id)
            ]
        for wanted in ("moved", "recorded", "duplicate"):
            if wanted in outcomes:
                return wanted
    return None


def _apply_remittance_in_practice(
    practice: PracticeContext, fetched: FetchedRemittance
) -> WebhookOutcome | None:
    for user_id in practice.user_ids:
        with tenant_db_session(practice.schema, user_id) as session:
            pipeline = ClaimPipeline(
                claims=PostgresClaimRepository(session),
                receipts=PostgresClaimReceiptRepository(session),
                session=session,
                principal_user_id=user_id,
            )
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
                    ),
                    payers=PostgresPayerRepository(session),
                    commit=session.commit,
                )
                work(run, user_id)
            completed += 1
        except Exception:  # one clinician's failure must not stop the next
            logger.exception(
                "claims_pipeline_clinician_failed schema=%s user_id=%s", practice.schema, user_id
            )
    return completed
