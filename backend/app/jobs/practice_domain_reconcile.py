# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Serve practice hosts: run the domain reconciler while it has work.

Brings the load balancer's certificates, certificate-map entries and host rules
into line with the hosts practices have added or removed in Settings > Domains
(``app.services.practice_domain_reconciler``).

When it runs. The backend asks for a run whenever it leaves the job something
to do — a host added or removed, or a DNS check that left a host waiting —
through the hook in ``app.services.practice_domain_trigger``; a deployment
registers what starts the job. A run then keeps going while it is useful:

* nothing pending, verifying or removing at the start: it ends at once,
  before any cloud client is made;
* otherwise it sweeps, and while any host is still in progress, sleeps
  :data:`SWEEP_INTERVAL` and sweeps again;
* it stops when nothing is in progress, after :data:`MAX_RUN`, or when two
  sweeps in a row changed nothing and every host still in progress is waiting
  on the practice (a record not added yet) rather than on the certificate's
  issuer. A practice that walked away does not hold the job open; its next
  change, or its next "Check now", asks for another run.

Add one scheduled run a day with ``--recheck``, which sweeps every host once
even when none is in progress: that is what notices a served host whose record
or certificate has lapsed, and retries a host that ended in ``error``.

Every step is idempotent and every write is guarded on what the sweep read, so
overlapping runs are safe. The web backend makes no cloud calls for this; only
this job does.

Configuration (``app.settings``):

* ``practice_domain_serving_project``, ``practice_domain_certificate_map``,
  ``practice_domain_url_map``, ``practice_domain_path_matcher`` — where the
  hosts are served from. All empty: the deployment does not serve hosts this
  way, and the job says so and exits 0.
* ``practice_domain_cname_target`` (or ``practice_domain_apex_ips``) — what a
  host must point at before it is served.

The job's identity needs, on the serving project,
``roles/certificatemanager.editor`` (DNS authorisations, certificates, map
entries) and ``roles/compute.loadBalancerAdmin`` (reading and updating the URL
map, which also uses the backend services it names and polls the operation).
It reads and writes ``platform.practice_domains`` and each practice's audit
log, through the database the backend uses.

A deployment that provisions email sending identities registers its
provisioner (``practice_domain_email.register_email_identity_provisioner``) and
then calls :func:`run`.

Invoked from repo ``backend/``::

    python -m app.jobs.practice_domain_reconcile            # on demand
    python -m app.jobs.practice_domain_reconcile --recheck  # once a day

Exit codes:
    * 0 — the run finished, or serving is not configured
    * 1 — the configuration is incomplete, or some practice's hosts could not
      be finished in the last sweep (each is retried next run)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from ..services.practice_domain_cloud import ServingConfig
from ..services.practice_domain_dns import get_dns_lookup
from ..services.practice_domain_email import email_identity_provisioner
from ..services.practice_domain_reconcile_store import PostgresReconcileStore
from ..services.practice_domain_reconciler import PracticeDomainReconciler
from ..services.practice_domain_service import get_practice_domain_service
from ..settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..services.practice_domain_cloud import DomainServing
    from ..services.practice_domain_reconciler import ReconcileStore, SweepReport

logger = logging.getLogger(__name__)

#: How long a run waits between sweeps while a host is in progress.
SWEEP_INTERVAL = timedelta(minutes=2)
#: How long one run may keep sweeping, from its start.
MAX_RUN = timedelta(minutes=45)
#: Sweeps in a row that change nothing before a run waiting only on practices stops.
QUIET_SWEEPS = 2

StopReason = Literal["done", "waiting_on_practice", "max_run"]


@dataclass(frozen=True)
class RunResult:
    sweeps: int
    stopped: StopReason
    last: SweepReport


def keep_sweeping(
    sweep: Callable[[], SweepReport],
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> RunResult:
    """Sweep, and keep sweeping every :data:`SWEEP_INTERVAL` while a host is
    in progress, until nothing is, :data:`MAX_RUN` is up, or every host left
    is waiting on its practice and :data:`QUIET_SWEEPS` sweeps changed nothing."""
    interval, limit = SWEEP_INTERVAL.total_seconds(), MAX_RUN.total_seconds()
    start = clock()
    sweeps = quiet = 0
    while True:
        report = sweep()
        sweeps += 1
        in_progress = report.in_progress
        if not in_progress:
            return RunResult(sweeps, "done", report)
        quiet = quiet + 1 if report.quiet else 0
        if quiet >= QUIET_SWEEPS and in_progress <= report.awaiting_practice:
            return RunResult(sweeps, "waiting_on_practice", report)
        if clock() - start + interval >= limit:
            return RunResult(sweeps, "max_run", report)
        sleep(interval)


def _google_serving(config: ServingConfig) -> DomainServing:
    from ..services.practice_domain_gcp import (  # noqa: PLC0415 — loads the cloud clients
        GoogleDomainServing,
    )

    return GoogleDomainServing(config)


def run(
    argv: list[str] | None = None,
    *,
    serving_factory: Callable[[ServingConfig], DomainServing] = _google_serving,
    store: ReconcileStore | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """One run. The keyword arguments replace the cloud, the database, the
    clock and the wait (tests)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = _parse_argv(argv)
    settings = get_settings()
    try:
        config = ServingConfig.from_settings(settings)
    except ValueError as e:
        problem: str | None = str(e)
    else:
        problem = None
    if problem is not None:
        logger.error("practice_domain_reconcile_misconfigured: %s", problem)
        return 1
    if config is None:
        logger.info(
            "practice_domain_reconcile_disabled: this deployment does not serve practice "
            "hosts (practice_domain_serving_project is not set); nothing to do"
        )
        return 0
    if not (settings.practice_domain_cname_target.strip() or settings.practice_domain_apex_ips):
        logger.error(
            "practice_domain_reconcile_misconfigured: set practice_domain_cname_target so a "
            "host's record can be checked before it is served"
        )
        return 1

    store = store or PostgresReconcileStore()
    if not args.recheck and not store.any_in_progress():
        logger.info("practice_domain_reconcile_idle: no host is in progress; nothing to do")
        return 0

    reconciler = PracticeDomainReconciler(
        serving=serving_factory(config),
        store=store,
        lookup=get_dns_lookup(),
        service_for=get_practice_domain_service,
        email=email_identity_provisioner(),
    )
    result = keep_sweeping(reconciler.sweep, clock=clock, sleep=sleep)
    logger.info(
        "practice_domain_reconcile_done sweeps=%s stopped=%s in_progress=%s failed_practices=%s",
        result.sweeps,
        result.stopped,
        len(result.last.in_progress),
        result.last.failed_practices,
    )
    return 1 if result.last.failed_practices else 0


def _parse_argv(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve practice hosts.")
    parser.add_argument(
        "--recheck",
        action="store_true",
        help="Sweep every host once even if none is in progress (the daily run).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run())
