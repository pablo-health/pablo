# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a practice types as its domain, and what is kept or refused."""

from __future__ import annotations

import pytest
from app.services.practice_domain_hosts import (
    HostnameError,
    deployment_hosts,
    normalize_host,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("portal.example.com", "portal.example.com"),
        ("  Portal.Example.COM  ", "portal.example.com"),
        ("portal.example.com.", "portal.example.com"),
        ("https://portal.example.com/", "portal.example.com"),
        ("http://Example.org", "example.org"),
        ("www.example.co.uk", "www.example.co.uk"),
        ("my-practice.example", "my-practice.example"),
        ("bücher.example", "xn--bcher-kva.example"),
        ("xn--bcher-kva.example", "xn--bcher-kva.example"),
    ],
)
def test_accepted_and_normalised(raw: str, expected: str) -> None:
    assert normalize_host(raw) == expected


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "Enter a domain."),
        ("   ", "Enter a domain."),
        ("https://portal.example.com/book", "Enter only the domain"),
        ("portal.example.com/path", "Enter only the domain"),
        ("portal.example.com?x=1", "Enter only the domain"),
        ("user@portal.example.com", "Enter only the domain"),
        ("portal.example.com:8443", "Enter only the domain"),
        ("*.example.com", "without *"),
        ("192.0.2.10", "not an IP address"),
        ("[2001:db8::1]", "not an IP address"),
        ("2001:db8::1", "not an IP address"),
        ("localhost", "valid domain"),
        ("portal.localhost", "valid domain"),
        ("intranet", "valid domain"),
        ("-bad.example.com", "valid domain"),
        ("bad-.example.com", "valid domain"),
        ("under_score.example.com", "valid domain"),
        ("two..dots.example", "valid domain"),
        ("1.2.3.999", "valid domain"),
        (f"{'a' * 64}.example.com", "valid domain"),
        (".".join(["abcdefghij"] * 25) + ".com", "valid domain"),
    ],
)
def test_refused_with_a_reason(raw: str, message: str) -> None:
    with pytest.raises(HostnameError, match=message.replace("*", r"\*")):
        normalize_host(raw)


def test_the_deployments_own_hosts_are_refused_but_names_under_them_are_not() -> None:
    reserved = deployment_hosts(
        ["https://app.example.org", "https://portal.example.org/", "", "sites.example.net"]
    )
    assert reserved == {"app.example.org", "portal.example.org", "sites.example.net"}

    for own in ("app.example.org", "PORTAL.example.org.", "sites.example.net"):
        with pytest.raises(HostnameError, match="part of this service"):
            normalize_host(own, reserved=reserved)
    assert normalize_host("clinic.app.example.org", reserved=reserved) == "clinic.app.example.org"
