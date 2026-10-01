# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Signing a Domain Connect apply link, checked the way a DNS provider checks it.

The provider rebuilds nothing: it strips ``sig`` and ``key`` from the query it
received and verifies the rest. So the golden test pins the exact bytes sent,
and the verification tests run the received string through
:func:`verify_signed_query`, the provider's procedure.

The captured test is the one that matters for the real thing: a signature
made once by the deployment's real Cloud KMS key, over a fixed sample, checks
against the public key as it is actually published in DNS (two TXT records,
captured in the order DNS returned them, which is not ``p`` order).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest
from app.services.domain_connect_signing import (
    KmsSigner,
    RsaKeySigner,
    SignatureError,
    apply_url,
    canonical_query,
    public_key_from_txt,
    signed_query,
    verify,
    verify_signed_query,
)
from cryptography.hazmat.primitives.asymmetric import rsa

FIXTURES = Path(__file__).parent / "fixtures" / "domain_connect"


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _FixedSigner:
    """Signature bytes whose base64 has ``+``, ``/`` and ``=`` in it."""

    def sign(self, message: bytes) -> bytes:
        return b"\xfb\xff\xfe\x01"


class TestCanonicalQuery:
    def test_sorts_by_encoded_name_and_encodes_per_rfc3986(self) -> None:
        params = {
            "text": "a+b",
            "domain": "example.net",
            "b": "2",
            "a": "1",
            "ip": "10.10.10.10",
        }
        # The spec's own example canonical input.
        assert canonical_query(params) == "a=1&b=2&domain=example.net&ip=10.10.10.10&text=a%2Bb"

    def test_leaves_only_unreserved_characters_bare(self) -> None:
        assert canonical_query({"v": "A-z_0.9~ /:?&=%"}) == "v=A-z_0.9~%20%2F%3A%3F%26%3D%25"


class TestApplyUrl:
    def test_golden(self) -> None:
        url = apply_url(
            "https://dns.example.net/connect/",
            "provider.example",
            "practice-domain",
            {
                "domain": "example.com",
                "verify": "test-token",
                "redirect_uri": "https://app.example.org/dashboard/settings/domains",
                "state": "abc.def",
                "portaltarget": "sites",
            },
            _FixedSigner(),
            "dev1",
        )
        assert url == (
            "https://dns.example.net/connect/v2/domainTemplates/providers/provider.example"
            "/services/practice-domain/apply"
            "?domain=example.com"
            "&portaltarget=sites"
            "&redirect_uri=https%3A%2F%2Fapp.example.org%2Fdashboard%2Fsettings%2Fdomains"
            "&state=abc.def"
            "&verify=test-token"
            "&sig=%2B%2F%2F%2BAQ%3D%3D"
            "&key=dev1"
        )

    def test_signature_and_key_come_last(self, private_key: rsa.RSAPrivateKey) -> None:
        url = apply_url(
            "https://dns.example.net",
            "provider.example",
            "svc",
            {"domain": "example.com", "z": "1"},
            RsaKeySigner(private_key),
            "dev1",
        )
        names = [name for name, _ in parse_qsl(urlsplit(url).query)]
        assert names == ["domain", "z", "sig", "key"]


class TestVerification:
    def test_a_signed_query_verifies_as_a_provider_checks_it(
        self, private_key: rsa.RSAPrivateKey
    ) -> None:
        query = signed_query(
            {"domain": "example.com", "certauth": "x+y/z", "state": "s.t"},
            RsaKeySigner(private_key),
            "dev1",
        )
        verify_signed_query(query, private_key.public_key())

    def test_a_changed_value_does_not_verify(self, private_key: rsa.RSAPrivateKey) -> None:
        query = signed_query(
            {"domain": "example.com", "verify": "test-token"}, RsaKeySigner(private_key), "dev1"
        )
        with pytest.raises(SignatureError):
            verify_signed_query(
                query.replace("example.com", "example.net"), private_key.public_key()
            )

    def test_another_key_does_not_verify(self, private_key: rsa.RSAPrivateKey) -> None:
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        query = signed_query({"domain": "example.com"}, RsaKeySigner(other), "dev1")
        with pytest.raises(SignatureError):
            verify_signed_query(query, private_key.public_key())


class TestPublishedKey:
    def _published(self) -> list[str]:
        answers = json.loads((FIXTURES / "dns_txt.json").read_text())["answers"]
        return list(answers["dev1.dc.pablo.health"])

    def test_the_real_kms_signature_verifies_against_the_published_key(self) -> None:
        records = self._published()
        # Captured out of order: reassembly must sort by p.
        assert records[0].startswith("p=2")
        key = public_key_from_txt(records)
        message = (FIXTURES / "kms_sample_input.txt").read_bytes()
        verify(key, message, (FIXTURES / "kms_sample_signature.bin").read_bytes())

    def test_the_sample_is_a_canonical_query(self) -> None:
        message = (FIXTURES / "kms_sample_input.txt").read_text()
        assert canonical_query(dict(parse_qsl(message))) == message

    def test_the_published_key_refuses_a_changed_message(self) -> None:
        key = public_key_from_txt(self._published())
        with pytest.raises(SignatureError):
            verify(
                key,
                (FIXTURES / "kms_sample_input.txt").read_bytes() + b"&x=1",
                (FIXTURES / "kms_sample_signature.bin").read_bytes(),
            )

    def test_fragments_in_the_wrong_order_are_not_the_key(self) -> None:
        records = self._published()
        swapped = [r.replace("p=1", "p=9") for r in records]
        with pytest.raises(SignatureError):
            public_key_from_txt(swapped)

    def test_an_algorithm_other_than_rs256_is_refused(self) -> None:
        with pytest.raises(SignatureError):
            public_key_from_txt(["p=1,a=ES256,d=AAAA"])


class TestKmsSigner:
    def test_asks_kms_to_sign_the_sha256_digest_with_the_configured_version(self) -> None:
        calls: list[dict[str, Any]] = []

        class _Client:
            def asymmetric_sign(self, request: dict[str, Any]) -> SimpleNamespace:
                calls.append(request)
                return SimpleNamespace(signature=b"test-signature")

        version = "projects/p/locations/l/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1"
        signature = KmsSigner(version, client=_Client()).sign(b"a=1&b=2")

        assert signature == b"test-signature"
        assert calls == [
            {"name": version, "digest": {"sha256": hashlib.sha256(b"a=1&b=2").digest()}}
        ]
