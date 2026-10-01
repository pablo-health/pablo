# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Serving a practice's hosts: bringing what the load balancer serves into line
with ``platform.practice_domains``.

One sweep visits every host, practice by practice, and takes it one step
further each time — every step is safe to repeat, so a sweep that stops half
way loses nothing:

1. Look the practice's records up (``PracticeDomainService.check``, the same
   check Settings > Domains runs), which also records a domain's ownership
   when its ``_pablo-verify`` TXT is found.
2. Per host, ask for its DNS authorisation (storing the ``_acme-challenge``
   value, so the practice is shown the record at once) and its certificate.
   ``pending`` becomes ``verifying``: the records are known and the job is
   waiting on the practice's DNS and on the certificate. What it is waiting
   for is kept in ``last_error``.
3. The issuer tries to authorise a certificate as soon as it is requested —
   usually before the practice has added the ``_acme-challenge`` record — and
   after that failed attempt it may not look again for a long time. So when a
   certificate is stuck behind a failed attempt and this sweep's check finds
   that record in place, the certificate is deleted and requested again
   against the same authorisation (the record stays right), at most once per
   :data:`REISSUE_INTERVAL` per host.
4. When the domain's ownership is confirmed, the host's own record points
   here, and the certificate is active: add the certificate-map entry and the
   URL-map host rule, and the host becomes ``active``.
5. ``error`` is kept for what the practice or whoever runs the deployment has
   to act on: a request the cloud refused, or a CAA record that forbids the
   certificate. A record not added yet is waiting, not an error.
6. An ``active`` host is checked again every sweep. If its record stops
   pointing here, or its certificate stops being active, it goes to ``error``
   with the reason — and its host rule stays: taking a working site down over
   a DNS blip would be worse than the blip. No answer in time changes nothing.
   It comes back to ``active`` by itself once both are right again.
7. A ``removing`` host has its host rule, map entry, certificate and DNS
   authorisation removed, in that order, and then its row is deleted; with the
   last host under a domain, the domain's row (and its email identity) go too.
   The hosts of a practice that is no longer active (``is_active`` false, or
   offboarded with ``deleted_at`` set, or gone) are made ``removing`` first.

Then, when the deployment registered one, each domain whose ownership is
confirmed has its email sending identity ensured (see
``practice_domain_email``).

Every status change is audited with the system as the actor, scoped to the
practice. Status writes are guarded on the status the sweep read, so a removal
the practice makes meanwhile is never overwritten. Cloud calls happen outside
any database transaction.

No PHI: hostnames, practice ids and setup state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol

from ..models.audit import AuditAction
from ..models.practice_domain import IN_PROGRESS_STATUSES, ServingState
from ..utcnow import utc_now
from .practice_domain_cloud import DomainServingError
from .practice_domain_hosts import apex_or_none
from .practice_domain_service import CERT_AUTH_LABEL, CERT_AUTH_SUFFIX, VERIFY_LABEL

if TYPE_CHECKING:
    from collections.abc import Callable, Collection
    from contextlib import AbstractContextManager
    from datetime import datetime

    from ..models.practice_domain import (
        DnsRecord,
        HostStatus,
        PracticeDomain,
        PracticeDomainApex,
    )
    from ..repositories.practice_domain import PracticeDomainRepository
    from .practice_domain_cloud import CertificateStatus, DomainServing
    from .practice_domain_dns import DnsLookup
    from .practice_domain_email import EmailIdentityProvisioner
    from .practice_domain_service import PracticeDomainService

logger = logging.getLogger(__name__)

#: ``last_error`` is VARCHAR(500).
_LAST_ERROR_MAX = 500
_POINTING_TYPES = frozenset({"A", "AAAA", "CNAME"})

PointingVerdict = Literal["ok", "bad", "unknown"]

#: The least time between two re-requests of one host's certificate, so a host
#: whose record never settles cannot churn certificates.
REISSUE_INTERVAL = timedelta(minutes=30)

POINTING_LAPSED = "The host's DNS record no longer points here."
CERTIFICATE_LAPSED = "The host's certificate is no longer active."
CERTIFICATE_PENDING = "Waiting for the certificate to be issued."
CERTIFICATE_RETRY = (
    "The certificate's last authorisation attempt failed; it is requested again "
    f"once its record is in place, at most every {REISSUE_INTERVAL.seconds // 60} minutes."
)
CERTIFICATE_RATE_LIMITED = "The certificate authority is rate-limiting this domain; waiting."


class PracticeScope(Protocol):
    """One unit of work for one practice: its hosts' writes and their audit."""

    @property
    def repo(self) -> PracticeDomainRepository: ...

    def audit(self, action: AuditAction, changes: dict[str, Any]) -> None: ...


class ReconcileStore(Protocol):
    def all_hosts(self) -> list[PracticeDomain]:
        """Every host of every practice, ``removing`` ones included."""
        ...

    def practice(self, practice_id: str) -> AbstractContextManager[PracticeScope]:
        """A unit of work for *practice_id*, committed when it closes cleanly."""
        ...

    def any_in_progress(self) -> bool:
        """Whether any host is pending, verifying or removing. Cheap: it is
        asked before anything else, so a run with nothing to do ends at once."""
        ...

    def retired_practices(self, practice_ids: Collection[str]) -> set[str]:
        """Those of *practice_ids* that are no longer active: deactivated,
        offboarded, or with no practice row at all."""
        ...


@dataclass
class SweepReport:
    hosts: int = 0
    #: Status changes written.
    changed: int = 0
    #: Rows written for any reason (a status, a certificate state, a reason).
    written: int = 0
    #: Removed hosts whose serving was taken down and whose row was deleted.
    released: int = 0
    #: Practices a sweep could not finish; their hosts are retried next sweep.
    failed_practices: int = 0
    #: Each host's status once the sweep was done with it.
    statuses: dict[str, HostStatus] = field(default_factory=dict)
    #: Hosts left waiting on something only the practice can do: a record not
    #: in place yet. Not the hosts waiting on the certificate's issuer.
    awaiting_practice: set[str] = field(default_factory=set)

    @property
    def in_progress(self) -> set[str]:
        """The hosts still pending, verifying or removing after the sweep."""
        return {d for d, status in self.statuses.items() if status in IN_PROGRESS_STATUSES}

    @property
    def quiet(self) -> bool:
        """Whether the sweep wrote nothing at all."""
        return self.written == 0 and self.released == 0


def pointing_verdict(host: str, records: list[DnsRecord]) -> PointingVerdict:
    """Whether a check found the host's own record(s) pointing here.

    A bare domain may be shown both an A and an AAAA record; one of them in
    place is enough, but any found pointing elsewhere sends some visitors
    there, so that counts against it. No answer in time is ``unknown``.
    """
    checks = {r.check for r in records if r.type in _POINTING_TYPES and r.name == host}
    if not checks or checks <= {"unknown", None}:
        return "unknown"
    if "wrong" in checks:
        return "bad"
    return "ok" if "ok" in checks else "bad"


def challenge_verdict(host: str, auth_value: str, records: list[DnsRecord]) -> PointingVerdict:
    """Whether a check found the host's ``_acme-challenge`` CNAME carrying
    *auth_value*. Not checked (the value was not stored yet when the check
    ran, or no answer in time) is ``unknown``."""
    name, value = challenge_record(host, auth_value)
    for record in records:
        if record.type == "CNAME" and record.name == name and record.value == value:
            if record.check in (None, "unknown"):
                return "unknown"
            return "ok" if record.check == "ok" else "bad"
    return "unknown"


def challenge_record(host: str, auth_value: str) -> tuple[str, str]:
    """The ``_acme-challenge`` CNAME's name and value for *host*."""
    return f"{CERT_AUTH_LABEL}.{host}", f"{auth_value}.{CERT_AUTH_SUFFIX}"


class PracticeDomainReconciler:
    def __init__(
        self,
        *,
        serving: DomainServing,
        store: ReconcileStore,
        lookup: DnsLookup,
        service_for: Callable[[PracticeDomainRepository], PracticeDomainService],
        email: EmailIdentityProvisioner | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._serving = serving
        self._store = store
        self._lookup = lookup
        self._service_for = service_for
        self._email = email
        self._now = now

    def sweep(self) -> SweepReport:
        report = SweepReport()
        by_practice: dict[str, list[PracticeDomain]] = {}
        for host in self._store.all_hosts():
            by_practice.setdefault(host.practice_id, []).append(host)
            report.statuses[host.domain] = host.status
        retired = self._store.retired_practices(by_practice.keys()) if by_practice else set()
        for practice_id, hosts in by_practice.items():
            report.hosts += len(hosts)
            try:
                if practice_id in retired:
                    self._retire(hosts, report)
                else:
                    self._practice(practice_id, hosts, report)
            except Exception:
                # One practice's failure must not keep the others from being
                # served; whatever it had left is retried next sweep.
                logger.exception(
                    "practice_domain_reconcile_practice_failed practice_id=%s", practice_id
                )
                report.failed_practices += 1
        return report

    def _practice(self, practice_id: str, hosts: list[PracticeDomain], report: SweepReport) -> None:
        live = [h for h in hosts if h.status != "removing"]
        records: dict[str, list[DnsRecord]] = {}
        apexes: dict[str, PracticeDomainApex] = {}
        if live:
            records, apexes = self._check_dns(practice_id)
        for host in hosts:
            if host.status == "removing":
                self._take_down(host, report)
            else:
                apex = apex_or_none(host.domain)
                self._bring_up(host, records.get(host.domain, []), apexes.get(apex or ""), report)
        if self._email is not None and live:
            self._email_identities(self._email, practice_id, live, apexes)

    def _retire(self, hosts: list[PracticeDomain], report: SweepReport) -> None:
        """Stop serving every host of a practice that is no longer active."""
        for host in hosts:
            if host.status != "removing":
                with self._store.practice(host.practice_id) as scope:
                    if not scope.repo.mark_removing(host.domain, host.practice_id):
                        continue
                    scope.audit(
                        AuditAction.PRACTICE_DOMAIN_STATUS_CHANGED,
                        {
                            "domain": host.domain,
                            "status": "removing",
                            "previous": host.status,
                            "reason": "practice_inactive",
                        },
                    )
                report.changed += 1
                report.written += 1
                report.statuses[host.domain] = "removing"
            self._take_down(replace(host, status="removing", is_primary=False), report)

    def _check_dns(
        self, practice_id: str
    ) -> tuple[dict[str, list[DnsRecord]], dict[str, PracticeDomainApex]]:
        with self._store.practice(practice_id) as scope:
            before = {
                a.apex for a in scope.repo.list_apexes_for_practice(practice_id) if a.verified_at
            }
            responses, found = self._service_for(scope.repo).check(practice_id, self._lookup)
            confirmed = sorted(set(found) - before)
            if confirmed:
                scope.audit(AuditAction.PRACTICE_DOMAIN_OWNERSHIP_CONFIRMED, {"domains": confirmed})
            apexes = {a.apex: a for a in scope.repo.list_apexes_for_practice(practice_id)}
        return {r.domain: r.dns_records for r in responses}, apexes

    # --- one host ------------------------------------------------------------

    def _bring_up(
        self,
        host: PracticeDomain,
        records: list[DnsRecord],
        apex: PracticeDomainApex | None,
        report: SweepReport,
    ) -> None:
        reissued_at = host.cert_reissued_at
        try:
            auth_value = self._serving.ensure_dns_authorization(host.domain)
            certificate = self._serving.ensure_certificate(host.domain)
            challenge = challenge_verdict(host.domain, auth_value, records)
            if self._may_reissue(host, certificate, challenge) and self._claim_reissue(host):
                logger.info("practice_domain_reconcile_reissue domain=%s", host.domain)
                reissued_at = self._now()
                certificate = self._serving.recreate_certificate(host.domain)
        except DomainServingError as e:
            self._refused(host, e, report)
            return
        found = replace(
            ServingState.of(host),
            cert_auth_value=auth_value,
            cert_status=certificate.state,
            cert_reissued_at=reissued_at,
        )

        pointing = pointing_verdict(host.domain, records)
        owned = apex is not None and apex.verified_at is not None
        if owned and pointing == "ok" and certificate.state == "ACTIVE":
            try:
                self._serving.ensure_map_entry(host.domain)
                self._serving.ensure_host_rule(host.domain)
            except DomainServingError as e:
                self._refused(host, e, report)
                return
            verified_at = host.verified_at if host.status == "active" else self._now()
            active = replace(found, status="active", last_error=None, verified_at=verified_at)
            self._write(host, active, report)
            return

        status, reason, on_practice = _waiting(
            host, auth_value, certificate, challenge=challenge, owned=owned, pointing=pointing
        )
        self._write(host, replace(found, status=status, last_error=reason), report)
        if on_practice:
            report.awaiting_practice.add(host.domain)

    def _claim_reissue(self, host: PracticeDomain) -> bool:
        """Record the re-request before making it, against the time last
        recorded, so two runs at once cannot both make it."""
        with self._store.practice(host.practice_id) as scope:
            return scope.repo.claim_reissue(host.domain, last=host.cert_reissued_at, at=self._now())

    def _may_reissue(
        self, host: PracticeDomain, certificate: CertificateStatus, challenge: PointingVerdict
    ) -> bool:
        """Whether to delete the certificate and request it again: it is stuck
        behind a failed attempt, its record is in place now, the issuer is not
        rate-limiting, and it was not re-requested within the interval."""
        if not certificate.stuck or challenge != "ok":
            return False
        if certificate.attempt_failure == "RATE_LIMITED":
            return False
        last = host.cert_reissued_at
        return last is None or self._now() - last >= REISSUE_INTERVAL

    def _refused(
        self, host: PracticeDomain, error: DomainServingError, report: SweepReport
    ) -> None:
        """A cloud call that did not go through.

        A transient failure is retried next sweep. A refusal puts a host that
        is not served yet in ``error``; a served host keeps its status, since
        the site still answers and the fault is the deployment's to fix.
        """
        logger.warning(
            "practice_domain_reconcile_call_failed domain=%s transient=%s: %s",
            host.domain,
            error.transient,
            error,
        )
        if error.transient or _served_before(host):
            return
        self._write(
            host, replace(ServingState.of(host), status="error", last_error=str(error)), report
        )

    def _take_down(self, host: PracticeDomain, report: SweepReport) -> None:
        try:
            # Host rule first, so the name stops routing before its
            # certificate goes; the map entry before the certificate it names.
            self._serving.remove_host_rule(host.domain)
            self._serving.remove_map_entry(host.domain)
            self._serving.remove_certificate(host.domain)
            self._serving.remove_dns_authorization(host.domain)
        except DomainServingError as e:
            logger.warning(
                "practice_domain_reconcile_take_down_failed domain=%s transient=%s: %s",
                host.domain,
                e.transient,
                e,
            )
            if not e.transient:
                self._write(host, replace(ServingState.of(host), last_error=str(e)), report)
            return

        apex = apex_or_none(host.domain)
        with self._store.practice(host.practice_id) as scope:
            if not scope.repo.delete_removing(host.domain):
                return
            scope.audit(AuditAction.PRACTICE_DOMAIN_RELEASED, {"domain": host.domain})
            report.released += 1
            report.statuses.pop(host.domain, None)
            remaining = scope.repo.list_for_practice(host.practice_id)
            if apex is None or any(apex_or_none(d.domain) == apex for d in remaining):
                return
            apex_row = scope.repo.get_apex(apex)
            if apex_row is None or apex_row.practice_id != host.practice_id:
                return
            if self._email is not None and apex_row.email_identity_status is not None:
                self._email.remove(apex)
            scope.repo.remove_apex(apex, host.practice_id)

    def _write(self, host: PracticeDomain, state: ServingState, report: SweepReport) -> None:
        if state.last_error is not None:
            state = replace(state, last_error=state.last_error[:_LAST_ERROR_MAX])
        if state == ServingState.of(host):
            return
        with self._store.practice(host.practice_id) as scope:
            if not scope.repo.record_serving(host.domain, expected_status=host.status, state=state):
                # Removed or changed since the sweep read it; the next sweep
                # starts from what it is now.
                logger.info("practice_domain_reconcile_skipped domain=%s", host.domain)
                return
            report.written += 1
            report.statuses[host.domain] = state.status
            if state.status != host.status:
                scope.audit(
                    AuditAction.PRACTICE_DOMAIN_STATUS_CHANGED,
                    {"domain": host.domain, "status": state.status, "previous": host.status},
                )
                report.changed += 1
                logger.info(
                    "practice_domain_reconcile_status domain=%s %s -> %s",
                    host.domain,
                    host.status,
                    state.status,
                )

    # --- email identities ----------------------------------------------------

    def _email_identities(
        self,
        email: EmailIdentityProvisioner,
        practice_id: str,
        live: list[PracticeDomain],
        apexes: dict[str, PracticeDomainApex],
    ) -> None:
        held = {apex_or_none(h.domain) for h in live}
        for name in sorted(a for a in held if a is not None):
            apex = apexes.get(name)
            if apex is None or apex.practice_id != practice_id or apex.verified_at is None:
                continue
            identity = email.ensure(name)
            tokens = list(identity.dkim_tokens) or None
            if (identity.status, tokens) == (apex.email_identity_status, apex.email_dkim_tokens):
                continue
            with self._store.practice(practice_id) as scope:
                scope.repo.set_email_identity(name, practice_id, identity.status, tokens)
                if identity.status != apex.email_identity_status:
                    scope.audit(
                        AuditAction.PRACTICE_DOMAIN_EMAIL_IDENTITY_CHANGED,
                        {"domain": name, "status": identity.status},
                    )


def _served_before(host: PracticeDomain) -> bool:
    """Whether the host has been active: ``verified_at`` is set when it first is."""
    return host.status == "active" or (host.status == "error" and host.verified_at is not None)


def _waiting(
    host: PracticeDomain,
    auth_value: str,
    certificate: CertificateStatus,
    *,
    challenge: PointingVerdict,
    owned: bool,
    pointing: PointingVerdict,
) -> tuple[HostStatus, str | None, bool]:
    """Where a host stands while something it needs is missing, why, and
    whether what it waits for is the practice's to do.

    A host that has been served and lapses is in ``error``. One that has not
    is ``verifying`` with what it waits for first — unless what is in the way
    is a CAA record, which only the practice can change.
    """
    if _served_before(host):
        if pointing == "bad":
            return "error", POINTING_LAPSED, False
        if certificate.state != "ACTIVE":
            return "error", CERTIFICATE_LAPSED, False
        return host.status, host.last_error, False
    if certificate.attempt_failure == "CAA":
        detail = certificate.detail or "CAA"
        reason = f"The domain's CAA records do not allow this certificate ({detail})."
        return "error", reason, False
    reason, on_practice = _waiting_for(
        host, auth_value, certificate, challenge=challenge, owned=owned, pointing=pointing
    )
    return "verifying", reason, on_practice


def _waiting_for(
    host: PracticeDomain,
    auth_value: str,
    certificate: CertificateStatus,
    *,
    challenge: PointingVerdict,
    owned: bool,
    pointing: PointingVerdict,
) -> tuple[str, bool]:
    """The first thing a host not yet served is waiting for, and whether it
    is the practice's to do (a record) rather than the issuer's."""
    if certificate.state != "ACTIVE" and challenge == "bad":
        name, value = challenge_record(host.domain, auth_value)
        return f"Waiting for the record {name} CNAME {value}.", True
    if not owned:
        apex = apex_or_none(host.domain) or host.domain
        return f"Waiting for the record {VERIFY_LABEL}.{apex} TXT.", True
    if pointing != "ok":
        return f"Waiting for {host.domain} to point here.", True
    if certificate.attempt_failure == "RATE_LIMITED":
        return CERTIFICATE_RATE_LIMITED, False
    if certificate.stuck:
        return CERTIFICATE_RETRY, False
    return CERTIFICATE_PENDING, False
