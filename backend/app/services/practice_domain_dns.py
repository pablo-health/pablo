# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading a practice's DNS back and comparing it with the records it was shown.

A check answers one question per record — is it there, with the value we
asked for? — and nothing more. It does not decide that a host works: a CNAME
in place says nothing about whether a certificate has been issued or the host
is being served. The one thing a check does record is ownership: the
``_pablo-verify`` TXT found with the domain's token (see
``PracticeDomainService.check``).

The lookup is injected (:class:`DnsLookup`), so tests answer from a table and
the end-to-end stack points :class:`DnspythonLookup` at its own name server.
Timeouts are short: a practice is waiting on the page, and a record that did
not answer in time is reported as ``unknown`` rather than missing.

No PHI: public DNS names and values.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import TYPE_CHECKING, Protocol

import dns.exception
import dns.resolver

from ..settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from ..models.practice_domain import DnsRecord, RecordCheck

logger = logging.getLogger(__name__)

#: How long one query may wait for a server, and how long one lookup may take
#: in all, retries included. A page is waiting.
QUERY_TIMEOUT_SECONDS = 1.5
LOOKUP_LIFETIME_SECONDS = 3.0

_POINTING_TYPES = frozenset({"A", "AAAA", "CNAME"})
_DEFAULT_DNS_PORT = 53


class DnsLookup(Protocol):
    def __call__(self, name: str, rdtype: str) -> list[str] | None:
        """The values at *name* for *rdtype*.

        An empty list when there are none (no such name, or no records of that
        type); ``None`` when no answer came back in time to tell. Names come
        back lowercase without the trailing dot; TXT strings are joined and
        otherwise left as they are.
        """
        ...


def _name(value: str) -> str:
    return value.strip().lower().rstrip(".")


def _address(value: str) -> str:
    """One spelling per address, so ``2001:DB8:0::7`` matches ``2001:db8::7``."""
    try:
        return ipaddress.ip_address(value.strip()).compressed
    except ValueError:
        return value.strip().lower()


def parse_nameservers(raw: str) -> list[tuple[str, int]]:
    """``"10.0.0.2, fake-dns:5353, [2001:db8::53]:53"`` → ``[(host, port), ...]``."""
    servers: list[tuple[str, int]] = []
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        if item.startswith("["):
            host, _, rest = item[1:].partition("]")
            port = int(rest.removeprefix(":") or _DEFAULT_DNS_PORT)
        elif item.count(":") == 1:
            host, _, port_text = item.partition(":")
            port = int(port_text)
        else:
            host, port = item, _DEFAULT_DNS_PORT
        servers.append((host, port))
    return servers


class DnspythonLookup:
    """Lookups through dnspython: the system's resolver, or named servers.

    A named server may be a hostname (as on a private network, where the name
    server is another service); it is resolved to an address once, here.
    """

    def __init__(self, nameservers: Sequence[tuple[str, int]] = ()) -> None:
        if nameservers:
            resolver = dns.resolver.Resolver(configure=False)
            addresses: list[str] = []
            ports: dict[str, int] = {}
            for host, port in nameservers:
                try:
                    address = socket.gethostbyname(host) if not _is_address(host) else host
                except OSError:
                    logger.warning("DNS server %s could not be resolved; skipping it", host)
                    continue
                addresses.append(address)
                ports[address] = port
            resolver.nameservers = addresses
            resolver.nameserver_ports = ports
        else:
            resolver = dns.resolver.Resolver()
        resolver.timeout = QUERY_TIMEOUT_SECONDS
        resolver.lifetime = LOOKUP_LIFETIME_SECONDS
        # Ask for what the zone holds now, not what a cache remembers.
        resolver.cache = None
        self._resolver = resolver

    def __call__(self, name: str, rdtype: str) -> list[str] | None:
        try:
            answer = self._resolver.resolve(name, rdtype, raise_on_no_answer=False)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.YXDOMAIN):
            return []
        except dns.exception.DNSException as e:
            logger.info("DNS lookup for %s %s gave no answer: %s", name, rdtype, type(e).__name__)
            return None
        if answer.rrset is None:
            return []
        values: list[str] = []
        for rdata in answer.rrset:
            if rdtype == "TXT":
                values.append(b"".join(rdata.strings).decode("utf-8", errors="replace"))
            elif rdtype == "CNAME":
                values.append(_name(rdata.target.to_text()))
            else:
                values.append(_name(rdata.to_text()))
        return values


def get_dns_lookup() -> DnsLookup:
    """The lookup a check uses: ``practice_domain_dns_nameservers`` if set,
    otherwise the system's resolver."""
    return DnspythonLookup(parse_nameservers(get_settings().practice_domain_dns_nameservers))


def _is_address(host: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET6 if ":" in host else socket.AF_INET, host)
    except OSError:
        return False
    return True


class DnsChecker:
    """One check run: compares records with what DNS holds, asking each
    question at most once.

    *cname_target* and *apex_ips* are the deployment's own (see
    ``PracticeDomainService``). A host's pointing record counts as in place
    when it resolves to addresses this deployment answers on — the
    ``apex_ips``, or the addresses the CNAME target resolves to — since a DNS
    provider that flattens a CNAME, or an ALIAS record, shows addresses where
    the CNAME was asked for.
    """

    def __init__(
        self, lookup: DnsLookup, *, cname_target: str = "", apex_ips: Iterable[str] = ()
    ) -> None:
        self._lookup = lookup
        self._cname_target = _name(cname_target)
        self._apex_ips = frozenset(_address(ip) for ip in apex_ips)
        self._answers: dict[tuple[str, str], list[str] | None] = {}
        self._allowed: frozenset[str] | None = None

    def check(self, record: DnsRecord, *, host: str) -> DnsRecord:
        """*record* with ``check`` and ``found`` filled in. *host* is the host
        the record belongs to, which tells its pointing record from the rest."""
        pointing = record.type in _POINTING_TYPES and _name(record.name) == host
        if record.type == "TXT":
            status, found = self._check_txt(record)
        elif record.type == "CNAME":
            status, found = self._check_cname(record, pointing=pointing)
        elif record.type in {"A", "AAAA"}:
            status, found = self._check_address(record)
        else:
            status, found = "unknown", None
        return record.model_copy(update={"check": status, "found": found})

    def _ask(self, name: str, rdtype: str) -> list[str] | None:
        key = (_name(name), rdtype)
        if key not in self._answers:
            self._answers[key] = self._lookup(key[0], rdtype)
        return self._answers[key]

    def _addresses_we_answer_on(self) -> frozenset[str]:
        if self._allowed is None:
            allowed = set(self._apex_ips)
            if self._cname_target:
                for rdtype in ("A", "AAAA"):
                    allowed.update(_address(a) for a in self._ask(self._cname_target, rdtype) or [])
            self._allowed = frozenset(allowed)
        return self._allowed

    def _check_txt(self, record: DnsRecord) -> tuple[RecordCheck, list[str] | None]:
        found = self._ask(record.name, "TXT")
        if found is None:
            return "unknown", None
        if record.value in found:
            return "ok", found
        return ("wrong" if found else "missing"), found

    def _check_cname(
        self, record: DnsRecord, *, pointing: bool
    ) -> tuple[RecordCheck, list[str] | None]:
        found = self._ask(record.name, "CNAME")
        if found is None:
            return "unknown", None
        if _name(record.value) in found:
            return "ok", found
        if not pointing:
            return ("wrong" if found else "missing"), found
        if found:
            # A CNAME to another name that lands on this deployment's
            # addresses works the same: www pointing at the bare domain, as
            # one-click setup publishes it.
            status, _ = self._check_flattened(record)
            return (status if status in {"ok", "unknown"} else "wrong"), found
        return self._check_flattened(record)

    def _check_flattened(self, record: DnsRecord) -> tuple[RecordCheck, list[str] | None]:
        """No CNAME at the host itself: a flattened CNAME or an ALIAS answers
        with addresses instead."""
        v4, v6 = self._ask(record.name, "A"), self._ask(record.name, "AAAA")
        if v4 is None or v6 is None:
            return "unknown", None
        addresses = [_address(a) for a in v4 + v6]
        if not addresses:
            return "missing", []
        allowed = self._addresses_we_answer_on()
        if allowed and set(addresses) <= allowed:
            return "ok", addresses
        return "wrong", addresses

    def _check_address(self, record: DnsRecord) -> tuple[RecordCheck, list[str] | None]:
        found = self._ask(record.name, record.type)
        if found is None:
            return "unknown", None
        if not found:
            return "missing", found
        # Every address has to be ours: one left over from an old host sends
        # some visitors there.
        found = [_address(a) for a in found]
        if _address(record.value) in found and set(found) <= self._addresses_we_answer_on():
            return "ok", found
        return "wrong", found
