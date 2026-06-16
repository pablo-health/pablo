# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Attestation trust verdict + admin hardware-key enforcement (PABLO-f00/gjw).

The certificate-chain validation itself is ``py_webauthn``'s job; these
tests cover the branches Pablo owns — an inert (unconfigured) trust store,
malformed attestation parsing, and the gated admin hardware-key dependency.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from app.auth import service as auth_svc
from app.auth.providers import VerifiedIdentity
from app.auth.service import require_admin_hardware_key
from app.services.passkey_attestation import AttestationVerifier, build_attestation_verifier
from fastapi import HTTPException


class TestAttestationVerifier:
    def test_unconfigured_store_never_verifies(self) -> None:
        verifier = build_attestation_verifier("")
        assert verifier.configured is False
        assert (
            verifier.evaluate(
                credential="{}",
                expected_challenge=b"x",
                expected_origin="http://localhost:3000",
                expected_rp_id="localhost",
                strict=True,  # even strict cannot reject with no roots configured
            )
            is False
        )

    def test_missing_roots_dir_is_inert(self, tmp_path) -> None:
        assert build_attestation_verifier(str(tmp_path / "nope")).configured is False

    def test_parse_handles_malformed_credential(self) -> None:
        assert AttestationVerifier._parse("not-json") == (None, False)
        assert AttestationVerifier._parse(json.dumps({"response": {}})) == (None, False)


def _request_with_passkey(passkey_claim: object) -> SimpleNamespace:
    claims = {} if passkey_claim is None else {"pablo_passkey": passkey_claim}
    identity = VerifiedIdentity(
        provider="firebase",
        subject_id="s",
        email="a@b.c",
        mfa_satisfied=True,
        claims=claims,
    )
    return SimpleNamespace(state=SimpleNamespace(verified_identity=identity))


def _settings(*, enabled: bool, dev: bool) -> SimpleNamespace:
    return SimpleNamespace(webauthn_admin_require_hardware_key=enabled, is_development=dev)


class TestAdminHardwareKeyGate:
    def test_flag_off_is_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(auth_svc, "get_settings", lambda: _settings(enabled=False, dev=False))
        user = object()
        assert require_admin_hardware_key(_request_with_passkey(None), user) is user

    def test_dev_bypass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(auth_svc, "get_settings", lambda: _settings(enabled=True, dev=True))
        user = object()
        assert require_admin_hardware_key(_request_with_passkey(None), user) is user

    def test_hardware_session_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(auth_svc, "get_settings", lambda: _settings(enabled=True, dev=False))
        user = object()
        request = _request_with_passkey({"hw": True, "att": False})
        assert require_admin_hardware_key(request, user) is user

    @pytest.mark.parametrize(
        "claim", [None, {"hw": False, "att": False}, {"att": True}, "not-a-dict"]
    )
    def test_non_hardware_session_rejected(
        self, monkeypatch: pytest.MonkeyPatch, claim: object
    ) -> None:
        monkeypatch.setattr(auth_svc, "get_settings", lambda: _settings(enabled=True, dev=False))
        with pytest.raises(HTTPException) as exc_info:
            require_admin_hardware_key(_request_with_passkey(claim), object())
        assert exc_info.value.status_code == 403
