# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Serving practice hosts on Google Cloud, against captured API responses.

The JSON under ``fixtures/practice_domain_gcp`` was captured from the real
Certificate Manager and Compute APIs through the same Python clients the code
uses (``Type.to_json``), then scrubbed: project, hostnames, ids and the
authorisation value replaced, structure kept. The certificate was captured
while provisioning; the ACTIVE and FAILED cases below change the state fields
of that capture rather than inventing a shape.

The clients are replaced by stubs that store and return those messages, so
what is exercised is the parsing, the naming, the order of calls and the
URL map's read-modify-write.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from app.services.practice_domain_cloud import (
    DomainServingError,
    ServingConfig,
    resource_id,
)
from app.services.practice_domain_gcp import (
    URL_MAP_EDIT_ATTEMPTS,
    GoogleDomainServing,
    cert_auth_value,
    certificate_status,
)
from google.api_core import exceptions as api_exceptions
from google.cloud import certificate_manager_v1 as cm
from google.cloud import compute_v1

FIXTURES = Path(__file__).parent / "fixtures" / "practice_domain_gcp"
CONFIG = ServingConfig(
    project="example-project",
    certificate_map="sites-cert-map",
    url_map="sites-urlmap",
    path_matcher="sites-paths",
)
PARENT = "projects/example-project/locations/global"
HOST = "portal.example.org"
NEW_HOST = "book.example.net"


def _load(message_type: Any, name: str) -> Any:
    return message_type.from_json((FIXTURES / f"{name}.json").read_text())


def _copy(message: Any) -> Any:
    return type(message).deserialize(type(message).serialize(message))


class _Done:
    def __init__(self, value: Any = None) -> None:
        self._value = value

    def result(self, timeout: float | None = None) -> Any:
        return self._value


class StubCertificates:
    """Certificate Manager, holding messages by resource name."""

    def __init__(self) -> None:
        self.resources: dict[str, Any] = {}
        self.calls: list[tuple[str, str]] = []
        self.error: Exception | None = None

    def put(self, message: Any) -> None:
        self.resources[message.name] = message

    def _get(self, name: str) -> Any:
        self.calls.append(("get", name))
        if self.error is not None:
            raise self.error
        if name not in self.resources:
            raise api_exceptions.NotFound(name)
        return _copy(self.resources[name])

    def _create(self, name: str, message: Any) -> _Done:
        self.calls.append(("create", name))
        message.name = name
        self.resources[name] = message
        return _Done(_copy(message))

    def _delete(self, name: str) -> _Done:
        self.calls.append(("delete", name))
        if name not in self.resources:
            raise api_exceptions.NotFound(name)
        del self.resources[name]
        return _Done()

    def get_dns_authorization(self, *, name: str) -> Any:
        return self._get(name)

    def create_dns_authorization(
        self, *, parent: str, dns_authorization_id: str, dns_authorization: Any
    ) -> _Done:
        host = dns_authorization.domain
        dns_authorization.dns_resource_record = cm.DnsAuthorization.DnsResourceRecord(
            name=f"_acme-challenge.{host}.",
            type_="CNAME",
            data="test-auth.11.authorize.certificatemanager.goog.",
        )
        return self._create(f"{parent}/dnsAuthorizations/{dns_authorization_id}", dns_authorization)

    def delete_dns_authorization(self, *, name: str) -> _Done:
        return self._delete(name)

    def get_certificate(self, *, name: str) -> Any:
        return self._get(name)

    def create_certificate(self, *, parent: str, certificate_id: str, certificate: Any) -> _Done:
        return self._create(f"{parent}/certificates/{certificate_id}", certificate)

    def delete_certificate(self, *, name: str) -> _Done:
        return self._delete(name)

    def get_certificate_map_entry(self, *, name: str) -> Any:
        return self._get(name)

    def create_certificate_map_entry(
        self, *, parent: str, certificate_map_entry_id: str, certificate_map_entry: Any
    ) -> _Done:
        return self._create(
            f"{parent}/certificateMapEntries/{certificate_map_entry_id}", certificate_map_entry
        )

    def delete_certificate_map_entry(self, *, name: str) -> _Done:
        return self._delete(name)


class StubUrlMaps:
    """The URL maps API: a stored map whose fingerprint changes on every write.

    *interleaved* edits are applied by "someone else" between a read and the
    write that follows it, each one making that write stale.
    """

    def __init__(self, url_map: Any, interleaved: list[str] | None = None) -> None:
        self.stored = url_map
        self.interleaved = list(interleaved or [])
        self.gets = 0
        self.writes: list[Any] = []
        self._version = 1

    def get(self, *, project: str, url_map: str) -> Any:
        assert (project, url_map) == (CONFIG.project, CONFIG.url_map)
        self.gets += 1
        return _copy(self.stored)

    def update(self, *, project: str, url_map: str, url_map_resource: Any) -> _Done:
        if self.interleaved:
            other = self.interleaved.pop(0)
            self.stored.host_rules.append(
                compute_v1.HostRule(hosts=[other], path_matcher=CONFIG.path_matcher)
            )
            self._bump()
        if url_map_resource.fingerprint != self.stored.fingerprint:
            raise api_exceptions.PreconditionFailed("Invalid fingerprint.")
        self.stored = _copy(url_map_resource)
        self._bump()
        self.writes.append(_copy(self.stored))
        return _Done()

    def _bump(self) -> None:
        self._version += 1
        self.stored.fingerprint = f"test-fingerprint-{self._version}"


@pytest.fixture
def certificates() -> StubCertificates:
    stub = StubCertificates()
    stub.put(_load(cm.DnsAuthorization, "dns_authorization"))
    stub.put(_load(cm.Certificate, "certificate"))
    stub.put(_load(cm.CertificateMapEntry, "certificate_map_entry"))
    return stub


@pytest.fixture
def url_maps() -> StubUrlMaps:
    return StubUrlMaps(_load(compute_v1.UrlMap, "url_map"))


@pytest.fixture
def serving(certificates: StubCertificates, url_maps: StubUrlMaps) -> GoogleDomainServing:
    return GoogleDomainServing(CONFIG, certificates=certificates, url_maps=url_maps)


def _hosts(url_map: Any) -> dict[str, str]:
    return {h: r.path_matcher for r in url_map.host_rules for h in r.hosts}


# --- parsing the captured shapes ---------------------------------------------


def test_the_authorisation_value_is_the_record_less_its_suffix() -> None:
    authorization = _load(cm.DnsAuthorization, "dns_authorization")
    assert cert_auth_value(authorization) == "test-auth.10"


def test_an_authorisation_with_an_unexpected_record_is_refused() -> None:
    authorization = _load(cm.DnsAuthorization, "dns_authorization")
    authorization.dns_resource_record.data = "elsewhere.example.com."
    with pytest.raises(DomainServingError) as raised:
        cert_auth_value(authorization)
    assert raised.value.transient is False


def test_a_provisioning_certificate_reads_as_provisioning_with_nothing_in_the_way() -> None:
    status = certificate_status(_load(cm.Certificate, "certificate"))
    assert status.state == "PROVISIONING"
    assert status.detail is None


def test_an_active_certificate_reads_as_active() -> None:
    certificate = _load(cm.Certificate, "certificate")
    managed = cm.Certificate.ManagedCertificate
    certificate.managed.state = managed.State.ACTIVE
    certificate.managed.authorization_attempt_info[
        0
    ].state = managed.AuthorizationAttemptInfo.State.AUTHORIZED
    assert certificate_status(certificate).state == "ACTIVE"


def test_a_failed_certificate_says_why() -> None:
    certificate = _load(cm.Certificate, "certificate")
    managed = cm.Certificate.ManagedCertificate
    certificate.managed.state = managed.State.FAILED
    attempt = certificate.managed.authorization_attempt_info[0]
    attempt.state = managed.AuthorizationAttemptInfo.State.FAILED
    attempt.failure_reason = managed.AuthorizationAttemptInfo.FailureReason.CAA
    attempt.details = "CAA forbids issuance"

    status = certificate_status(certificate)

    assert status.state == "FAILED"
    assert status.detail == f"{HOST}: CAA CAA forbids issuance"


# --- certificates, authorisations and map entries ------------------------------


def test_existing_resources_are_adopted_without_creating_anything(
    serving: GoogleDomainServing, certificates: StubCertificates
) -> None:
    assert serving.ensure_dns_authorization(HOST) == "test-auth.10"
    assert serving.ensure_certificate(HOST).state == "PROVISIONING"
    serving.ensure_map_entry(HOST)

    assert [c for c in certificates.calls if c[0] == "create"] == []


def test_a_new_host_gets_its_resources_named_after_it(
    serving: GoogleDomainServing, certificates: StubCertificates
) -> None:
    assert serving.ensure_dns_authorization(NEW_HOST) == "test-auth.11"
    serving.ensure_certificate(NEW_HOST)
    serving.ensure_map_entry(NEW_HOST)

    assert [name for kind, name in certificates.calls if kind == "create"] == [
        f"{PARENT}/dnsAuthorizations/dnsauth-book-example-net",
        f"{PARENT}/certificates/cm-book-example-net",
        f"{PARENT}/certificateMaps/sites-cert-map/certificateMapEntries/book-example-net",
    ]
    certificate = certificates.resources[f"{PARENT}/certificates/cm-book-example-net"]
    assert list(certificate.managed.domains) == [NEW_HOST]
    assert list(certificate.managed.dns_authorizations) == [
        f"{PARENT}/dnsAuthorizations/dnsauth-book-example-net"
    ]
    entry = certificates.resources[
        f"{PARENT}/certificateMaps/sites-cert-map/certificateMapEntries/book-example-net"
    ]
    assert entry.hostname == NEW_HOST
    assert list(entry.certificates) == [f"{PARENT}/certificates/cm-book-example-net"]


def test_a_resource_made_for_another_host_is_not_adopted(
    serving: GoogleDomainServing, certificates: StubCertificates
) -> None:
    # portal-example.org slugs exactly as portal.example.org does.
    with pytest.raises(DomainServingError, match="exists for another host") as raised:
        serving.ensure_dns_authorization("portal-example.org")
    assert raised.value.transient is False


def test_removal_deletes_only_what_belongs_to_the_host_and_tolerates_absence(
    serving: GoogleDomainServing, certificates: StubCertificates
) -> None:
    serving.remove_map_entry("portal-example.org")
    serving.remove_certificate("portal-example.org")
    serving.remove_dns_authorization("portal-example.org")
    assert [c for c in certificates.calls if c[0] == "delete"] == []

    serving.remove_map_entry(HOST)
    serving.remove_certificate(HOST)
    serving.remove_dns_authorization(HOST)
    assert certificates.resources == {}

    serving.remove_map_entry(HOST)
    serving.remove_certificate(HOST)
    serving.remove_dns_authorization(HOST)


@pytest.mark.parametrize(
    ("error", "transient"),
    [
        (api_exceptions.ServiceUnavailable("busy"), True),
        (api_exceptions.DeadlineExceeded("slow"), True),
        (api_exceptions.PermissionDenied("no"), False),
        (api_exceptions.InvalidArgument("bad hostname"), False),
    ],
)
def test_a_client_error_says_whether_it_is_worth_retrying(
    serving: GoogleDomainServing,
    certificates: StubCertificates,
    error: Exception,
    transient: bool,
) -> None:
    certificates.error = error
    with pytest.raises(DomainServingError) as raised:
        serving.ensure_certificate(HOST)
    assert raised.value.transient is transient
    assert str(raised.value).startswith("Certificate: ")


# --- the URL map --------------------------------------------------------------


def test_a_host_already_routed_writes_nothing(
    serving: GoogleDomainServing, url_maps: StubUrlMaps
) -> None:
    serving.ensure_host_rule(HOST)
    assert url_maps.writes == []


def test_a_new_host_gets_a_rule_and_nothing_else_changes(
    serving: GoogleDomainServing, url_maps: StubUrlMaps
) -> None:
    before = _copy(url_maps.stored)

    serving.ensure_host_rule(NEW_HOST)

    after = url_maps.stored
    assert _hosts(after) == {HOST: "sites-paths", NEW_HOST: "sites-paths"}
    assert after.path_matchers == before.path_matchers
    assert after.default_url_redirect == before.default_url_redirect


def test_an_edit_that_lost_a_race_is_reread_and_keeps_the_other_edit(
    serving: GoogleDomainServing, url_maps: StubUrlMaps
) -> None:
    url_maps.interleaved = ["other.example.com", "third.example.com"]

    serving.ensure_host_rule(NEW_HOST)

    assert url_maps.gets == 3
    assert set(_hosts(url_maps.stored)) == {
        HOST,
        NEW_HOST,
        "other.example.com",
        "third.example.com",
    }


def test_an_edit_that_keeps_losing_is_retried_next_sweep(
    serving: GoogleDomainServing, url_maps: StubUrlMaps
) -> None:
    url_maps.interleaved = [f"h{i}.example.com" for i in range(URL_MAP_EDIT_ATTEMPTS)]

    with pytest.raises(DomainServingError) as raised:
        serving.ensure_host_rule(NEW_HOST)

    assert raised.value.transient is True
    assert NEW_HOST not in _hosts(url_maps.stored)


def test_a_missing_path_matcher_is_refused(certificates: StubCertificates) -> None:
    url_map = _load(compute_v1.UrlMap, "url_map")
    url_map.path_matchers[0].name = "something-else"
    serving = GoogleDomainServing(CONFIG, certificates=certificates, url_maps=StubUrlMaps(url_map))

    with pytest.raises(DomainServingError, match="no path matcher") as raised:
        serving.ensure_host_rule(NEW_HOST)
    assert raised.value.transient is False


def test_a_host_routed_by_a_rule_it_did_not_make_is_left_alone(
    certificates: StubCertificates,
) -> None:
    url_map = _load(compute_v1.UrlMap, "url_map")
    url_map.host_rules[0].path_matcher = "another-matcher"
    stub = StubUrlMaps(url_map)
    serving = GoogleDomainServing(CONFIG, certificates=certificates, url_maps=stub)

    with pytest.raises(DomainServingError, match="another host rule"):
        serving.ensure_host_rule(HOST)
    serving.remove_host_rule(HOST)

    assert stub.writes == []


def test_removing_a_host_drops_its_rule_and_keeps_the_rest(
    certificates: StubCertificates,
) -> None:
    url_map = _load(compute_v1.UrlMap, "url_map")
    url_map.host_rules[0].hosts.append("www.example.org")
    url_map.host_rules.append(compute_v1.HostRule(hosts=[NEW_HOST], path_matcher="sites-paths"))
    stub = StubUrlMaps(url_map)
    serving = GoogleDomainServing(CONFIG, certificates=certificates, url_maps=stub)

    serving.remove_host_rule(HOST)
    serving.remove_host_rule(NEW_HOST)
    serving.remove_host_rule(NEW_HOST)

    assert _hosts(stub.stored) == {"www.example.org": "sites-paths"}
    assert len(stub.writes) == 2


# --- naming -------------------------------------------------------------------


def test_resource_ids_follow_the_hostname() -> None:
    assert resource_id("cm-", HOST) == "cm-portal-example-org"
    assert resource_id("", HOST) == "portal-example-org"


def test_a_long_host_gets_a_short_distinct_id() -> None:
    long_a = f"{'a' * 60}.example.org"
    long_b = f"{'a' * 60}.example.com"
    ids = {resource_id("dnsauth-", long_a), resource_id("dnsauth-", long_b)}
    assert len(ids) == 2
    assert all(len(i) <= 63 and i.startswith("dnsauth-") for i in ids)


def test_a_host_that_cannot_start_an_id_still_gets_one() -> None:
    name = resource_id("", "1st.example.org")
    assert name[0].isalpha()
    assert len(name) <= 63
