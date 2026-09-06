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


WebhookOutcome = Literal["moved", "recorded", "duplicate", "unmatched", "ignored"]

#: A webhook delivery is bounded by the vendor's response timeout; it
#: cannot visit an unbounded registry.
_WEBHOOK_MAX_TENANTS = 50


def ingest_transaction_event(event: WebhookEvent) -> WebhookOutcome:
    """Apply one ``transaction.processed`` delivery to whichever practice owns it.

    The transaction is fetched through each practice's account in turn (an
    account that does not own it answers 404); the first practice whose
    clinician can see the claim it names is the one that handles it. An
    inbound document that is not a 277CA is ``ignored``. Vendor outages
    propagate so the receiver can ask for a redelivery.
    """
    transaction_id = event.transaction_id
    if transaction_id is None:
        return "ignored"
    outcome: WebhookOutcome = "unmatched"
    for practice in active_practices(max_tenants=_WEBHOOK_MAX_TENANTS):
        try:
            fetched = fetch_acknowledgment(practice.client, transaction_id)
        except ClearinghouseNotFoundError:
            continue
        if fetched is None:
            return "ignored"
        applied = _apply_in_practice(practice, fetched, event.id)
        if applied is not None:
            return applied
    return outcome


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
