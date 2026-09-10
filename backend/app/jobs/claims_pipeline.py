# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The claims pipeline on a schedule: send, ask, and watch.

Four stages, run in order for every active practice with a clearinghouse
configured, clinician by clinician in that clinician's own tenant session
(see ``app.claims.fanout``):

* ``submit`` — file every ``validated`` claim through the outbox
  (``app.claims.submit_worker``);
* ``status`` — read the feed for claims still waiting on an
  acknowledgement (``app.claims.status_worker``);
* ``remit`` — read each waiting claim's timeline and post what the payer
  decided (``app.claims.remittance``). After ``status`` on purpose: a claim
  the payer accepted in this same run can then be paid in it too, rather
  than waiting a whole interval to notice;
* ``watchdog`` — stall what has timed out and raise the deadline ladder
  (``app.claims.watchdog``).

Bounded twice: ``--max-tenants`` caps how many practices one run visits
and ``--max-per-tenant`` how many claims each stage handles per clinician.
One clinician's failure is logged and the run moves on.

Runs inside the API process every ``CLAIMS_PIPELINE_INTERVAL_MINUTES``
when ``CLAIMS_PIPELINE_ENABLED`` is on (the default for a self-hosted
deployment); a deployment with its own scheduler turns that off and
invokes this from repo ``backend/`` instead::

    python -m app.jobs.claims_pipeline
    python -m app.jobs.claims_pipeline --stage submit --max-tenants 50

Exit codes:
    * 0 — success
    * 1 — unexpected error
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import Counter
from dataclasses import asdict
from typing import TYPE_CHECKING

from ..claims.credentials import get_clearinghouse_credential_provider
from ..claims.fanout import (
    PracticeContext,
    TenantRun,
    active_practices,
    for_each_clinician,
    load_submission_account,
)
from ..claims.receipts import owned_by_principal
from ..claims.remittance import post_remittances
from ..claims.remittance_feed import FeedRemittanceDetails
from ..claims.routing import record_claim_route
from ..claims.sdk_timeline import SdkClaimTimelines
from ..claims.status_worker import AWAITING_STATES, poll_acknowledgments
from ..claims.submit_worker import submit_pending
from ..claims.watchdog import run_watchdog
from ..db import create_standalone_session
from ..settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..claims.sdk_timeline import ClaimTimelineSource
    from ..claims.submit_worker import SubmissionAccount

logger = logging.getLogger(__name__)

STAGES: tuple[str, ...] = ("submit", "status", "remit", "watchdog")
DEFAULT_MAX_TENANTS = 500
DEFAULT_MAX_PER_TENANT = 200


def _account_for(practice: PracticeContext) -> SubmissionAccount | None:
    with create_standalone_session(practice.schema) as session:
        return load_submission_account(session, practice.practice_id)


def _timelines_for(practice: PracticeContext) -> ClaimTimelineSource | None:
    """Where this practice's claim timelines come from, if anywhere.

    ``None`` on a deployment with no clearinghouse configured, which is the
    ordinary case for one that does not bill insurance at all.
    """
    credentials = get_clearinghouse_credential_provider().get(practice.practice_id)
    if credentials is None:
        return None
    return SdkClaimTimelines(credentials)


def run_practice(
    practice: PracticeContext, stages: Sequence[str], *, max_per_tenant: int
) -> Counter[str]:
    """Run ``stages`` for one practice; returns per-stage counts."""
    totals: Counter[str] = Counter()
    account = _account_for(practice) if "submit" in stages else None
    if "submit" in stages and account is None:
        logger.info("claims_pipeline_cannot_file schema=%s reason=billing_profile", practice.schema)
    timelines = _timelines_for(practice) if "remit" in stages else None
    # One feed scan for the whole practice, built here rather than per
    # clinician so forty claims do not walk the same pages forty times. It
    # reads nothing until a claim is actually adjudicated.
    details = FeedRemittanceDetails(practice.client) if timelines is not None else None

    def work(run: TenantRun, _user_id: str) -> None:
        if account is not None:
            submitted = submit_pending(
                run.pipeline,
                practice.client,
                account,
                payers=run.payers,
                practice_user_ids=practice.user_ids,
                commit=run.commit,
                limit=max_per_tenant,
                # This run knows which practice it is; the worker deliberately
                # does not. Recording the pair here is what lets a webhook
                # route by lookup later (PABLO-ffw8).
                on_pending=lambda control: record_claim_route(control, practice.practice_id),
            )
            totals.update({f"submit_{k}": v for k, v in asdict(submitted).items()})
        if "status" in stages:
            polled = poll_acknowledgments(
                run.pipeline,
                practice.client,
                practice_user_ids=practice.user_ids,
                limit=max_per_tenant,
            )
            totals.update({f"status_{k}": v for k, v in asdict(polled).items()})
        if timelines is not None:
            # After the acknowledgement pass, so a claim the payer accepted
            # in this same run can be paid in it too rather than waiting a
            # whole interval to notice.
            waiting = [
                claim
                for claim in run.pipeline.claims.list_by_state(
                    AWAITING_STATES, limit=max_per_tenant
                )
                if owned_by_principal(run.pipeline, claim, practice.user_ids)
            ]
            totals["remit_read"] += len(waiting)
            totals["remit_posted"] += post_remittances(
                run.pipeline, timelines, waiting, details=details
            )
        if "watchdog" in stages:
            watched = run_watchdog(
                run.pipeline,
                payers=run.payers,
                practice_user_ids=practice.user_ids,
                limit=max_per_tenant,
            )
            totals.update({f"watchdog_{k}": v for k, v in asdict(watched).items()})

    totals["clinicians"] = for_each_clinician(practice, work)
    return totals


def run_pipeline(
    stages: Sequence[str] = STAGES,
    *,
    max_tenants: int = DEFAULT_MAX_TENANTS,
    max_per_tenant: int = DEFAULT_MAX_PER_TENANT,
) -> Counter[str]:
    """Run ``stages`` across every practice with a clearinghouse; returns the counts."""
    totals: Counter[str] = Counter()
    for practice in active_practices(max_tenants=max_tenants):
        totals["practices"] += 1
        totals.update(run_practice(practice, stages, max_per_tenant=max_per_tenant))
    logger.info("claims_pipeline_done %s", " ".join(f"{k}={v}" for k, v in sorted(totals.items())))
    return totals


async def claims_pipeline_loop() -> None:
    """Background loop for a self-hosted deployment: the whole pipeline, every interval."""
    while True:
        await asyncio.sleep(get_settings().claims_pipeline_interval_minutes * 60)
        try:
            await asyncio.to_thread(run_pipeline)
        except Exception:
            logger.exception("claims_pipeline_loop_error")


def run(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = _parse_argv(argv)
    stages = STAGES if args.stage == "all" else (args.stage,)
    run_pipeline(stages, max_tenants=args.max_tenants, max_per_tenant=args.max_per_tenant)
    return 0


def _parse_argv(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=(*STAGES, "all"),
        default="all",
        help="Which stage to run (default: all of them, in order).",
    )
    parser.add_argument(
        "--max-tenants",
        type=int,
        default=DEFAULT_MAX_TENANTS,
        help=f"How many practices one run visits, in schema order (default {DEFAULT_MAX_TENANTS}).",
    )
    parser.add_argument(
        "--max-per-tenant",
        type=int,
        default=DEFAULT_MAX_PER_TENANT,
        help=(
            f"How many claims each stage handles per clinician (default {DEFAULT_MAX_PER_TENANT})."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run())
