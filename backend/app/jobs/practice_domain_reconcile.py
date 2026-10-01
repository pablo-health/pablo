# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Serve practice hosts: one sweep of the domain reconciler.

Brings the load balancer's certificates, certificate-map entries and host rules
into line with the hosts practices have added or removed in Settings > Domains
(``app.services.practice_domain_reconciler``). Run it on a schedule — every few
minutes keeps a newly pointed host from waiting long — and on demand after a
change. Every step is idempotent, so overlapping or repeated runs are safe.

The web backend makes no cloud calls for this; only this job does.

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

    python -m app.jobs.practice_domain_reconcile

Exit codes:
    * 0 — a sweep finished, or serving is not configured
    * 1 — the configuration is incomplete, or some practice's hosts could not
      be finished this sweep (each is retried next sweep)
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from ..services.practice_domain_cloud import ServingConfig
from ..services.practice_domain_dns import get_dns_lookup
from ..services.practice_domain_email import email_identity_provisioner
from ..services.practice_domain_reconcile_store import PostgresReconcileStore
from ..services.practice_domain_reconciler import PracticeDomainReconciler
from ..services.practice_domain_service import get_practice_domain_service
from ..settings import get_settings

if TYPE_CHECKING:
    from ..services.practice_domain_cloud import DomainServing

logger = logging.getLogger(__name__)


def run(serving: DomainServing | None = None) -> int:
    """One sweep. *serving* replaces the Google Cloud calls (tests)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
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

    if serving is None:
        from ..services.practice_domain_gcp import (  # noqa: PLC0415 — loads the cloud clients
            GoogleDomainServing,
        )

        serving = GoogleDomainServing(config)
    reconciler = PracticeDomainReconciler(
        serving=serving,
        store=PostgresReconcileStore(),
        lookup=get_dns_lookup(),
        service_for=get_practice_domain_service,
        email=email_identity_provisioner(),
    )
    report = reconciler.sweep()
    logger.info(
        "practice_domain_reconcile_done hosts=%s changed=%s released=%s failed_practices=%s",
        report.hosts,
        report.changed,
        report.released,
        report.failed_practices,
    )
    return 1 if report.failed_practices else 0


if __name__ == "__main__":
    sys.exit(run())
