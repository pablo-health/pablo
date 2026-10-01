# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A stand-in for the cloud a practice's hosts are served from.

Holds the four per-host resources as sets, records every call in order, and
lets a test set a host's certificate state or make a call fail.
"""

from __future__ import annotations

from app.services.practice_domain_cloud import CertificateStatus, DomainServingError


class FakeDomainServing:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.authorizations: dict[str, str] = {}
        self.certificates: set[str] = set()
        self.map_entries: set[str] = set()
        self.host_rules: set[str] = set()
        #: A host's certificate state; PROVISIONING when unset.
        self.certificate_state: dict[str, CertificateStatus] = {}
        #: The state a host's certificate is in once requested again;
        #: PROVISIONING when unset.
        self.after_reissue: dict[str, CertificateStatus] = {}
        #: Method name -> the error it raises.
        self.failures: dict[str, DomainServingError] = {}

    def _call(self, method: str, host: str) -> None:
        self.calls.append((method, host))
        if method in self.failures:
            raise self.failures[method]

    def created(self) -> list[tuple[str, str]]:
        """The calls that would have created something (an ``ensure_*`` of a
        resource that was not there)."""
        return [c for c in self.calls if c[0].startswith("create:")]

    def ensure_dns_authorization(self, host: str) -> str:
        self._call("ensure_dns_authorization", host)
        if host not in self.authorizations:
            self.calls.append(("create:dns_authorization", host))
            self.authorizations[host] = f"test-auth.{len(self.authorizations) + 1}"
        return self.authorizations[host]

    def ensure_certificate(self, host: str) -> CertificateStatus:
        self._call("ensure_certificate", host)
        if host not in self.certificates:
            self.calls.append(("create:certificate", host))
            self.certificates.add(host)
        return self.certificate_state.get(host, CertificateStatus("PROVISIONING"))

    def recreate_certificate(self, host: str) -> CertificateStatus:
        self._call("recreate_certificate", host)
        self.calls.append(("create:certificate", host))
        self.certificates.add(host)
        self.certificate_state[host] = self.after_reissue.get(
            host, CertificateStatus("PROVISIONING")
        )
        return self.certificate_state[host]

    def ensure_map_entry(self, host: str) -> None:
        self._call("ensure_map_entry", host)
        if host not in self.map_entries:
            self.calls.append(("create:map_entry", host))
            self.map_entries.add(host)

    def ensure_host_rule(self, host: str) -> None:
        self._call("ensure_host_rule", host)
        if host not in self.host_rules:
            self.calls.append(("create:host_rule", host))
            self.host_rules.add(host)

    def remove_host_rule(self, host: str) -> None:
        self._call("remove_host_rule", host)
        self.host_rules.discard(host)

    def remove_map_entry(self, host: str) -> None:
        self._call("remove_map_entry", host)
        self.map_entries.discard(host)

    def remove_certificate(self, host: str) -> None:
        self._call("remove_certificate", host)
        self.certificates.discard(host)

    def remove_dns_authorization(self, host: str) -> None:
        self._call("remove_dns_authorization", host)
        self.authorizations.pop(host, None)
