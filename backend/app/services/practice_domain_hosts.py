# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Turning what a practice typed into a hostname it can serve from, or saying why not.

A practice pastes whatever its DNS provider or browser showed it, so the common
near-misses — a scheme, a trailing slash, capitals, a trailing dot — are fixed
silently or named plainly. What is refused outright: anything that is not a
single DNS name (IP addresses, ports, paths, wildcards), names with no dot, and
the hosts this deployment itself answers on.

Internationalised names are stored in their ASCII (punycode) form, which is the
form DNS and certificates use.
"""

from __future__ import annotations

import ipaddress
import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Iterable

_MAX_HOST_LENGTH = 253
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_URL_PARTS = re.compile(r"[/?#@\\]")

ENTER_ONLY_THE_DOMAIN = "Enter only the domain, like portal.example.com."


class HostnameError(ValueError):
    """What is wrong with the name, in words the practice can act on."""


def _to_ascii(host: str) -> str:
    if host.isascii():
        return host
    try:
        return ".".join(label.encode("idna").decode("ascii") for label in host.split("."))
    except UnicodeError as e:
        raise HostnameError("That isn't a valid domain name.") from e


def _is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def deployment_hosts(urls: Iterable[str]) -> frozenset[str]:
    """The hostnames named by this deployment's own URLs or bare hosts."""
    hosts: set[str] = set()
    for url in urls:
        value = url.strip()
        if not value:
            continue
        host = urlsplit(value if "//" in value else f"//{value}").hostname
        if host:
            hosts.add(host.lower().rstrip("."))
    return frozenset(hosts)


def normalize_host(raw: str, *, reserved: frozenset[str] = frozenset()) -> str:
    """The canonical hostname for *raw*, or :class:`HostnameError`.

    *reserved* are this deployment's own hosts, which cannot belong to a
    practice.
    """
    host = raw.strip().lower()
    if not host:
        raise HostnameError("Enter a domain.")
    if "://" in host:
        host = host.split("://", 1)[1]
    # A single trailing slash is what a browser's address bar leaves behind.
    host = host.removesuffix("/")
    if _URL_PARTS.search(host):
        raise HostnameError(ENTER_ONLY_THE_DOMAIN)
    if _is_ip_address(host):
        raise HostnameError("Enter a domain name, not an IP address.")
    if ":" in host:
        raise HostnameError(ENTER_ONLY_THE_DOMAIN)
    if "*" in host:
        raise HostnameError("Enter one domain at a time, without *.")
    host = host.removesuffix(".")

    host = _to_ascii(host)
    labels = host.split(".")
    if (
        len(host) > _MAX_HOST_LENGTH
        or len(labels) < 2  # noqa: PLR2004 — a name with no dot is not on the public internet
        or not all(_LABEL.match(label) for label in labels)
        or labels[-1].isdigit()
        or labels[-1] == "localhost"
    ):
        raise HostnameError("That isn't a valid domain name.")

    # Exact matches only. A self-hosted install may serve the app from a
    # practice's own apex and want the portal on a name under it.
    if host in reserved:
        raise HostnameError("That address is part of this service. Enter a domain you own.")
    return host
