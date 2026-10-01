# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Checking a practice's DNS records: each kind of record there, missing,
wrong, or not answered in time.

The checker is driven by a table standing in for DNS. The last class runs
the real dnspython lookup against the stand-in name server the end-to-end
stack uses (``scripts/fake_dns.py``) over UDP on localhost, which proves the
wire-level half — TXT strings, CNAME targets, NXDOMAIN — without leaving the
machine.
"""

from __future__ import annotations

import importlib.util
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from app.models.practice_domain import DnsRecord
from app.services.practice_domain_dns import DnsChecker, DnspythonLookup, parse_nameservers

if TYPE_CHECKING:
    from collections.abc import Iterator

TARGET = "sites.example.net"
TARGET_IPS = ["198.51.100.10"]
HOST = "portal.ours.example"


def _lookup(table: dict[tuple[str, str], list[str] | None]) -> Any:
    asked: list[tuple[str, str]] = []

    def lookup(name: str, rdtype: str) -> list[str] | None:
        asked.append((name, rdtype))
        return table.get((name, rdtype), [])

    lookup.asked = asked  # type: ignore[attr-defined]  # test bookkeeping
    return lookup


def _check(
    table: dict[tuple[str, str], list[str] | None],
    record: DnsRecord,
    *,
    host: str = HOST,
    apex_ips: tuple[str, ...] = (),
) -> DnsRecord:
    checker = DnsChecker(_lookup(table), cname_target=TARGET, apex_ips=apex_ips)
    return checker.check(record, host=host)


VERIFY = DnsRecord(type="TXT", name="_pablo-verify.ours.example", value="pablo-verify=Tok_en-1")
POINT = DnsRecord(type="CNAME", name=HOST, value=TARGET)
CERT = DnsRecord(
    type="CNAME",
    name=f"_acme-challenge.{HOST}",
    value="uuid-1.7.authorize.certificatemanager.goog",
)
DKIM = DnsRecord(type="CNAME", name="k1._domainkey.ours.example", value="k1.dkim.amazonses.com")


class TestOwnershipTxt:
    def test_found(self) -> None:
        table = {(VERIFY.name, "TXT"): ["v=spf1 -all", "pablo-verify=Tok_en-1"]}
        result = _check(table, VERIFY)
        assert (result.check, result.found) == ("ok", ["v=spf1 -all", "pablo-verify=Tok_en-1"])

    def test_missing(self) -> None:
        assert _check({}, VERIFY).check == "missing"

    def test_another_token_is_wrong(self) -> None:
        result = _check({(VERIFY.name, "TXT"): ["pablo-verify=old"]}, VERIFY)
        assert (result.check, result.found) == ("wrong", ["pablo-verify=old"])

    def test_the_token_is_case_sensitive(self) -> None:
        assert _check({(VERIFY.name, "TXT"): ["pablo-verify=tok_en-1"]}, VERIFY).check == "wrong"

    def test_no_answer_in_time_is_unknown_not_missing(self) -> None:
        result = _check({(VERIFY.name, "TXT"): None}, VERIFY)
        assert (result.check, result.found) == ("unknown", None)


class TestHostCname:
    def test_found(self) -> None:
        assert _check({(HOST, "CNAME"): [TARGET]}, POINT).check == "ok"

    def test_pointing_elsewhere_is_wrong(self) -> None:
        result = _check({(HOST, "CNAME"): ["old-host.example"]}, POINT)
        assert (result.check, result.found) == ("wrong", ["old-host.example"])

    def test_missing(self) -> None:
        assert _check({}, POINT).check == "missing"

    def test_a_flattened_cname_to_our_addresses_is_ok(self) -> None:
        table = {(HOST, "A"): TARGET_IPS, (TARGET, "A"): TARGET_IPS}
        result = _check(table, POINT)
        assert (result.check, result.found) == ("ok", TARGET_IPS)

    def test_addresses_that_are_not_ours_are_wrong(self) -> None:
        table = {(HOST, "A"): ["192.0.2.99"], (TARGET, "A"): TARGET_IPS}
        result = _check(table, POINT)
        assert (result.check, result.found) == ("wrong", ["192.0.2.99"])

    def test_no_answer_for_the_addresses_is_unknown(self) -> None:
        assert _check({(HOST, "AAAA"): None}, POINT).check == "unknown"


class TestCertAuthCname:
    def test_found(self) -> None:
        assert _check({(CERT.name, "CNAME"): [CERT.value]}, CERT).check == "ok"

    def test_wrong(self) -> None:
        assert (
            _check(
                {(CERT.name, "CNAME"): ["uuid-0.1.authorize.certificatemanager.goog"]}, CERT
            ).check
            == "wrong"
        )

    def test_missing_is_not_rescued_by_addresses(self) -> None:
        """Only the host's own pointing record may be satisfied by addresses."""
        table = {(CERT.name, "A"): TARGET_IPS, (TARGET, "A"): TARGET_IPS}
        assert _check(table, CERT).check == "missing"


class TestDkimCname:
    def test_found_with_a_trailing_dot_and_capitals(self) -> None:
        # The lookup normalises names; a stub that did not would still match.
        assert _check({(DKIM.name, "CNAME"): ["k1.dkim.amazonses.com"]}, DKIM).check == "ok"

    def test_wrong(self) -> None:
        assert _check({(DKIM.name, "CNAME"): ["k1.dkim.other.example"]}, DKIM).check == "wrong"

    def test_missing(self) -> None:
        assert _check({}, DKIM).check == "missing"


class TestBareDomainAddresses:
    BARE = "ours.example"
    A = DnsRecord(type="A", name=BARE, value="203.0.113.7")
    AAAA = DnsRecord(type="AAAA", name=BARE, value="2001:db8::7")
    IPS = ("203.0.113.7", "2001:DB8:0::7")

    def test_found(self) -> None:
        table = {(self.BARE, "A"): ["203.0.113.7"], (self.BARE, "AAAA"): ["2001:db8::7"]}
        assert _check(table, self.A, host=self.BARE, apex_ips=self.IPS).check == "ok"
        assert _check(table, self.AAAA, host=self.BARE, apex_ips=self.IPS).check == "ok"

    def test_an_address_left_from_an_old_host_is_wrong(self) -> None:
        table = {(self.BARE, "A"): ["203.0.113.7", "192.0.2.1"]}
        result = _check(table, self.A, host=self.BARE, apex_ips=self.IPS)
        assert result.check == "wrong"
        assert result.found == ["203.0.113.7", "192.0.2.1"]

    def test_missing(self) -> None:
        assert _check({}, self.A, host=self.BARE, apex_ips=self.IPS).check == "missing"

    def test_unknown(self) -> None:
        table: dict[tuple[str, str], list[str] | None] = {(self.BARE, "A"): None}
        assert _check(table, self.A, host=self.BARE, apex_ips=self.IPS).check == "unknown"


def test_each_question_is_asked_once_per_check() -> None:
    lookup = _lookup({})
    checker = DnsChecker(lookup, cname_target=TARGET)
    checker.check(POINT, host=HOST)
    checker.check(POINT, host=HOST)
    assert len(lookup.asked) == len(set(lookup.asked))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("10.0.0.2", [("10.0.0.2", 53)]),
        (" fake-dns:5353 , 10.0.0.2 ", [("fake-dns", 5353), ("10.0.0.2", 53)]),
        ("[2001:db8::53]:5353", [("2001:db8::53", 5353)]),
        ("2001:db8::53", [("2001:db8::53", 53)]),
    ],
)
def test_parse_nameservers(raw: str, expected: list[tuple[str, int]]) -> None:
    assert parse_nameservers(raw) == expected


def _load_fake_dns() -> Any:
    path = Path(__file__).resolve().parents[2] / "scripts" / "fake_dns.py"
    spec = importlib.util.spec_from_file_location("fake_dns_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestOverTheWire:
    @pytest.fixture
    def fake(self) -> Iterator[tuple[Any, int]]:
        module = _load_fake_dns()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = module.serve_udp("127.0.0.1", port)
        try:
            yield module.zone, port
        finally:
            server.shutdown()
            server.server_close()

    def test_txt_cname_addresses_and_nothing(self, fake: tuple[Any, int]) -> None:
        zone, port = fake
        zone.set("_pablo-verify.ours.example", "TXT", ['pablo-verify=a"b'])
        zone.set("portal.ours.example", "CNAME", ["Sites.Example.NET."])
        zone.set("sites.example.net", "A", ["198.51.100.10"])
        lookup = DnspythonLookup([("127.0.0.1", port)])

        assert lookup("_pablo-verify.ours.example", "TXT") == ['pablo-verify=a"b']
        assert lookup("portal.ours.example", "CNAME") == ["sites.example.net"]
        # An address question follows the CNAME.
        assert lookup("portal.ours.example", "A") == ["198.51.100.10"]
        assert lookup("portal.ours.example", "AAAA") == []
        assert lookup("nothing-here.example", "TXT") == []

    def test_a_server_that_does_not_answer_is_unknown(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as silent:
            silent.bind(("127.0.0.1", 0))
            port = silent.getsockname()[1]
            assert DnspythonLookup([("127.0.0.1", port)])("ours.example", "TXT") is None
