# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Serving practice hosts from a Google Cloud HTTPS load balancer.

The load balancer's target proxy carries a Certificate Manager certificate map
and nothing else, and its URL map's host rules are the list of hosts it
answers for. Per host this keeps, in ``location global``:

* ``dnsauth-<host>`` — a DNS authorisation. Its CNAME is the record the
  practice adds, and it lets the certificate issue before the host points here.
* ``cm-<host>`` — a Google-managed certificate using that authorisation.
* ``<host>`` — an entry in the certificate map handing that certificate out.
* a host rule on the URL map sending the hostname to the configured path
  matcher -- or, for a website served from object storage, to a path matcher
  of the practice's own (``site-<practice>``: every path rewritten into the
  live version's folder on the backend bucket) or to a redirect to the
  website's primary host (``alias-<host>``).

(``<host>`` with dots as hyphens; see ``practice_domain_cloud.resource_id``.)

A certificate stuck behind a failed authorisation is deleted and created again
against the same DNS authorisation; its map entry, which pins it, is taken out
first and put back after.

The URL map is one shared resource, so every edit is read-modify-write against
its fingerprint, retried when another edit got there first. An edit touches
only host rules naming the host it is about, and the ``site-`` / ``alias-``
path matchers it makes itself — never the configured path matcher, the
default, or a rule it did not make.

Only the domain reconciler job imports this, and only when the deployment
serves practice hosts, so nothing else loads the clients.
"""

from __future__ import annotations

import concurrent.futures
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from google.api_core import exceptions as api_exceptions
from google.cloud import certificate_manager_v1, compute_v1

from ..sites.service import version_prefix
from .practice_domain_cloud import (
    CertificateStatus,
    DomainServingError,
    HostRoute,
    alias_matcher_id,
    resource_id,
    site_matcher_id,
)
from .practice_domain_service import CERT_AUTH_SUFFIX

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from .practice_domain_cloud import ServingConfig

logger = logging.getLogger(__name__)

#: How long one create or delete may take to finish. These are quick on
#: Google's side (issuing the certificate is not part of creating it).
OPERATION_TIMEOUT_SECONDS = 120.0
#: How many times a URL-map edit is retried after another edit changed the
#: map between our read and our write.
URL_MAP_EDIT_ATTEMPTS = 5

_TRANSIENT = (
    # Another run created the same resource between our read and our create;
    # the next sweep finds and adopts it.
    api_exceptions.AlreadyExists,
    api_exceptions.ServiceUnavailable,
    api_exceptions.DeadlineExceeded,
    api_exceptions.InternalServerError,
    api_exceptions.TooManyRequests,
    api_exceptions.Aborted,
    api_exceptions.RetryError,
    concurrent.futures.TimeoutError,
)


@contextmanager
def _calls(what: str) -> Iterator[None]:
    """Turn a client error into a :class:`DomainServingError` saying *what* failed."""
    try:
        yield
    except DomainServingError:
        raise
    except _TRANSIENT as e:
        raise DomainServingError(f"{what}: {type(e).__name__}", transient=True) from e
    except api_exceptions.GoogleAPICallError as e:
        raise DomainServingError(f"{what}: {type(e).__name__}: {e.message}", transient=False) from e


def _get[T](getter: Callable[..., T], name: str) -> T | None:
    try:
        return getter(name=name)
    except api_exceptions.NotFound:
        return None


def _delete(deleter: Callable[..., Any], name: str) -> None:
    try:
        deleter(name=name).result(timeout=OPERATION_TIMEOUT_SECONDS)
    except api_exceptions.NotFound:
        return


def cert_auth_value(authorization: certificate_manager_v1.DnsAuthorization) -> str:
    """The CNAME value a practice adds, before ``.authorize.certificatemanager.goog``."""
    data = authorization.dns_resource_record.data.strip().lower().rstrip(".")
    suffix = f".{CERT_AUTH_SUFFIX}"
    if not data.endswith(suffix):
        msg = f"DNS authorisation {authorization.name} has an unexpected record"
        raise DomainServingError(msg, transient=False)
    return data.removesuffix(suffix)


def certificate_status(certificate: certificate_manager_v1.Certificate) -> CertificateStatus:
    """What a managed certificate's state is, and what is in its way."""
    managed_type = certificate_manager_v1.Certificate.ManagedCertificate
    managed = certificate.managed
    problems: list[str] = []
    attempt_failure: str | None = None
    issue = managed.provisioning_issue
    if issue.reason != managed_type.ProvisioningIssue.Reason.REASON_UNSPECIFIED:
        problems.append(" ".join(filter(None, [issue.reason.name, issue.details])))
    for attempt in managed.authorization_attempt_info:
        if attempt.state == managed_type.AuthorizationAttemptInfo.State.FAILED:
            attempt_failure = attempt.failure_reason.name
            problems.append(
                " ".join(filter(None, [f"{attempt.domain}:", attempt_failure, attempt.details]))
            )
    detail = "; ".join(problems) or None
    if managed.state == managed_type.State.ACTIVE:
        return CertificateStatus("ACTIVE")
    if managed.state == managed_type.State.FAILED:
        return CertificateStatus("FAILED", detail, attempt_failure)
    return CertificateStatus("PROVISIONING", detail, attempt_failure)


def _belongs(what: str, name: str, found: list[str], host: str) -> None:
    """Refuse to adopt a resource made for another host (see ``resource_id``)."""
    if found != [host]:
        msg = f"{what} {name} exists for another host"
        raise DomainServingError(msg, transient=False)


class GoogleDomainServing:
    """:class:`~app.services.practice_domain_cloud.DomainServing` on Google Cloud."""

    def __init__(
        self,
        config: ServingConfig,
        *,
        certificates: certificate_manager_v1.CertificateManagerClient | None = None,
        url_maps: compute_v1.UrlMapsClient | None = None,
    ) -> None:
        self._certificates = certificates or certificate_manager_v1.CertificateManagerClient()
        self._url_maps = url_maps or compute_v1.UrlMapsClient()
        self._config = config
        self._parent = f"projects/{config.project}/locations/global"
        self._map = f"{self._parent}/certificateMaps/{config.certificate_map}"

    def _authorization_name(self, host: str) -> str:
        return f"{self._parent}/dnsAuthorizations/{resource_id('dnsauth-', host)}"

    def _certificate_name(self, host: str) -> str:
        return f"{self._parent}/certificates/{resource_id('cm-', host)}"

    def _entry_name(self, host: str) -> str:
        return f"{self._map}/certificateMapEntries/{resource_id('', host)}"

    # --- ensure --------------------------------------------------------------

    def ensure_dns_authorization(self, host: str) -> str:
        name = self._authorization_name(host)
        with _calls("DNS authorisation"):
            authorization = _get(self._certificates.get_dns_authorization, name)
            if authorization is None:
                logger.info("practice_domain_serving creating DNS authorisation for %s", host)
                authorization = self._certificates.create_dns_authorization(
                    parent=self._parent,
                    dns_authorization_id=name.rsplit("/", 1)[1],
                    dns_authorization=certificate_manager_v1.DnsAuthorization(domain=host),
                ).result(timeout=OPERATION_TIMEOUT_SECONDS)
        _belongs("DNS authorisation", name, [authorization.domain], host)
        return cert_auth_value(authorization)

    def ensure_certificate(self, host: str) -> CertificateStatus:
        name = self._certificate_name(host)
        with _calls("Certificate"):
            certificate = _get(self._certificates.get_certificate, name)
            if certificate is None:
                certificate = self._create_certificate(host)
        _belongs("Certificate", name, list(certificate.managed.domains), host)
        return certificate_status(certificate)

    def recreate_certificate(self, host: str) -> CertificateStatus:
        name, entry_name = self._certificate_name(host), self._entry_name(host)
        with _calls("Certificate (requesting it again)"):
            certificate = _get(self._certificates.get_certificate, name)
            if certificate is not None:
                _belongs("Certificate", name, list(certificate.managed.domains), host)
            entry = _get(self._certificates.get_certificate_map_entry, entry_name)
            mapped = entry is not None and entry.hostname == host
            if mapped:
                _delete(self._certificates.delete_certificate_map_entry, entry_name)
            if certificate is not None:
                _delete(self._certificates.delete_certificate, name)
            certificate = self._create_certificate(host)
        if mapped:
            self.ensure_map_entry(host)
        return certificate_status(certificate)

    def _create_certificate(self, host: str) -> certificate_manager_v1.Certificate:
        name = self._certificate_name(host)
        logger.info("practice_domain_serving requesting a certificate for %s", host)
        created: certificate_manager_v1.Certificate = self._certificates.create_certificate(
            parent=self._parent,
            certificate_id=name.rsplit("/", 1)[1],
            certificate=certificate_manager_v1.Certificate(
                managed=certificate_manager_v1.Certificate.ManagedCertificate(
                    domains=[host],
                    dns_authorizations=[self._authorization_name(host)],
                )
            ),
        ).result(timeout=OPERATION_TIMEOUT_SECONDS)
        return created

    def ensure_map_entry(self, host: str) -> None:
        name = self._entry_name(host)
        with _calls("Certificate map entry"):
            entry = _get(self._certificates.get_certificate_map_entry, name)
            if entry is None:
                logger.info("practice_domain_serving adding %s to the certificate map", host)
                entry = self._certificates.create_certificate_map_entry(
                    parent=self._map,
                    certificate_map_entry_id=name.rsplit("/", 1)[1],
                    certificate_map_entry=certificate_manager_v1.CertificateMapEntry(
                        hostname=host, certificates=[self._certificate_name(host)]
                    ),
                ).result(timeout=OPERATION_TIMEOUT_SECONDS)
        _belongs("Certificate map entry", name, [entry.hostname], host)

    def ensure_host_rule(self, host: str, route: HostRoute | None = None) -> None:
        route = route or HostRoute.default()
        matcher = self._matcher_name(route)

        def add(url_map: compute_v1.UrlMap) -> bool:
            changed = self._ensure_matcher(url_map, route)
            for rule in url_map.host_rules:
                if host not in rule.hosts:
                    continue
                if rule.path_matcher == matcher:
                    return changed
                if not self._ours(rule.path_matcher):
                    msg = f"{host} is routed by another host rule on {url_map.name}"
                    raise DomainServingError(msg, transient=False)
                # Moving the host: off a rule of ours, onto the matcher it
                # needs now. A rule naming other hosts too keeps them.
                others = [h for h in rule.hosts if h != host]
                if others:
                    rule.hosts = others
                else:
                    url_map.host_rules.remove(rule)
                break
            url_map.host_rules.append(compute_v1.HostRule(hosts=[host], path_matcher=matcher))
            self._drop_orphaned_matchers(url_map)
            return True

        self._edit_url_map(f"routing {host} to {matcher}", add)

    def invalidate_host(self, host: str) -> None:
        project, name = self._config.project, self._config.url_map
        with _calls(f"URL map (invalidating the cache for {host})"):
            self._url_maps.invalidate_cache(
                project=project,
                url_map=name,
                cache_invalidation_rule_resource=compute_v1.CacheInvalidationRule(
                    host=host, path="/*"
                ),
            ).result(timeout=OPERATION_TIMEOUT_SECONDS)
        logger.info("practice_domain_serving invalidated the cache for %s", host)

    # --- remove --------------------------------------------------------------

    def remove_host_rule(self, host: str) -> None:
        def drop(url_map: compute_v1.UrlMap) -> bool:
            kept: list[compute_v1.HostRule] = []
            changed = False
            for rule in url_map.host_rules:
                if host not in rule.hosts or not self._ours(rule.path_matcher):
                    kept.append(rule)
                    continue
                changed = True
                others = [h for h in rule.hosts if h != host]
                if others:
                    rule.hosts = others
                    kept.append(rule)
            if changed:
                url_map.host_rules = kept
                self._drop_orphaned_matchers(url_map)
            return changed

        self._edit_url_map(f"removing the host rule for {host}", drop)

    def remove_map_entry(self, host: str) -> None:
        name = self._entry_name(host)
        with _calls("Certificate map entry"):
            entry = _get(self._certificates.get_certificate_map_entry, name)
            if entry is not None and entry.hostname == host:
                _delete(self._certificates.delete_certificate_map_entry, name)

    def remove_certificate(self, host: str) -> None:
        name = self._certificate_name(host)
        with _calls("Certificate"):
            certificate = _get(self._certificates.get_certificate, name)
            if certificate is not None and list(certificate.managed.domains) == [host]:
                _delete(self._certificates.delete_certificate, name)

    def remove_dns_authorization(self, host: str) -> None:
        name = self._authorization_name(host)
        with _calls("DNS authorisation"):
            authorization = _get(self._certificates.get_dns_authorization, name)
            if authorization is not None and authorization.domain == host:
                _delete(self._certificates.delete_dns_authorization, name)

    # --- the URL map ---------------------------------------------------------

    def _matcher_name(self, route: HostRoute) -> str:
        if route.kind == "site":
            return site_matcher_id(route.practice_id)
        if route.kind == "alias":
            return alias_matcher_id(route.target)
        return self._config.path_matcher

    def _ours(self, matcher: str) -> bool:
        """Whether a host rule pointing at *matcher* is one this serving made:
        the configured matcher, or a practice-site or alias matcher."""
        return matcher == self._config.path_matcher or matcher.startswith(("site-", "alias-"))

    def _ensure_matcher(self, url_map: compute_v1.UrlMap, route: HostRoute) -> bool:
        """Make the path matcher *route* needs, or bring it up to date (a site
        matcher's folder follows the live version). Returns whether the map
        changed. The configured matcher must already be there."""
        name = self._matcher_name(route)
        current = next((m for m in url_map.path_matchers if m.name == name), None)
        if route.kind == "default":
            if current is None:
                msg = f"URL map {url_map.name} has no path matcher {name}"
                raise DomainServingError(msg, transient=False)
            return False
        wanted = self._site_matcher(route) if route.kind == "site" else self._alias_matcher(route)
        if current is not None and _same_matcher(current, wanted):
            return False
        if current is not None:
            url_map.path_matchers.remove(current)
        url_map.path_matchers.append(wanted)
        return True

    def _site_matcher(self, route: HostRoute) -> compute_v1.PathMatcher:
        backend = self._config.site_backend
        if not backend:
            msg = "serving a website from object storage needs practice_domain_site_backend"
            raise DomainServingError(msg, transient=False)
        bucket = (
            f"https://www.googleapis.com/compute/v1/projects/{self._config.project}"
            f"/global/backendBuckets/{backend}"
        )
        folder = "/" + version_prefix(route.practice_id, route.version)
        return compute_v1.PathMatcher(
            name=site_matcher_id(route.practice_id),
            default_service=bucket,
            route_rules=[
                compute_v1.HttpRouteRule(
                    priority=1,
                    match_rules=[compute_v1.HttpRouteRuleMatch(prefix_match="/")],
                    service=bucket,
                    route_action=compute_v1.HttpRouteAction(
                        url_rewrite=compute_v1.UrlRewrite(path_prefix_rewrite=folder)
                    ),
                )
            ],
        )

    @staticmethod
    def _alias_matcher(route: HostRoute) -> compute_v1.PathMatcher:
        return compute_v1.PathMatcher(
            name=alias_matcher_id(route.target),
            default_url_redirect=compute_v1.HttpRedirectAction(
                host_redirect=route.target,
                https_redirect=True,
                redirect_response_code="MOVED_PERMANENTLY_DEFAULT",
                strip_query=False,
            ),
        )

    @staticmethod
    def _drop_orphaned_matchers(url_map: compute_v1.UrlMap) -> None:
        """Drop the ``site-`` and ``alias-`` matchers no host rule points at."""
        routed = {rule.path_matcher for rule in url_map.host_rules}
        url_map.path_matchers = [
            m
            for m in url_map.path_matchers
            if m.name in routed or not m.name.startswith(("site-", "alias-"))
        ]

    def _edit_url_map(self, what: str, change: Callable[[compute_v1.UrlMap], bool]) -> None:
        """Read the map, let *change* edit it, and write it back against the
        fingerprint it was read with. *change* returns whether it changed
        anything; nothing is written if not."""
        project, name = self._config.project, self._config.url_map
        with _calls(f"URL map ({what})"):
            for _ in range(URL_MAP_EDIT_ATTEMPTS):
                url_map = self._url_maps.get(project=project, url_map=name)
                if not change(url_map):
                    return
                try:
                    self._url_maps.update(
                        project=project, url_map=name, url_map_resource=url_map
                    ).result(timeout=OPERATION_TIMEOUT_SECONDS)
                except api_exceptions.PreconditionFailed:
                    logger.info("practice_domain_serving URL map changed under the edit; retrying")
                    continue
                logger.info("practice_domain_serving %s", what)
                return
        msg = f"URL map ({what}): it kept changing under the edit"
        raise DomainServingError(msg, transient=True)


def _same_matcher(current: compute_v1.PathMatcher, wanted: compute_v1.PathMatcher) -> bool:
    """Whether *current* already says what *wanted* says. Compared through
    the wire form, so defaults the API filled in on read do not count."""
    as_json = compute_v1.PathMatcher.to_json
    return bool(
        as_json(current, including_default_value_fields=False)
        == as_json(wanted, including_default_value_fields=False)
    )
