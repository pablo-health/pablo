# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PasskeyService logic: enrolment gate (H4), clone detection (H7), mint (H1).

The ``py_webauthn`` signature-verify calls are monkeypatched so these
tests exercise the service's own decision logic deterministically,
without a real authenticator.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from app.models.passkey import PasskeyCredential
from app.repositories.identity import InMemoryIdentityRepository
from app.repositories.passkey_credential import InMemoryPasskeyCredentialRepository
from app.services import passkey_service as svc
from app.services.passkey_challenge_store import InMemoryPasskeyChallengeStore
from app.services.passkey_service import (
    PasskeyAssertionError,
    PasskeyEnrollmentError,
    PasskeyService,
)
from app.settings import get_settings
from app.utcnow import utc_now
from webauthn.helpers import bytes_to_base64url

CHALLENGE = b"\xaa\xbb\xcc\xdd" * 8
USER_ID = "11111111-1111-4111-8111-111111111111"
CRED_ID = "cred-abc"


def _build_service() -> tuple[
    PasskeyService, InMemoryPasskeyCredentialRepository, InMemoryPasskeyChallengeStore
]:
    credentials = InMemoryPasskeyCredentialRepository()
    challenges = InMemoryPasskeyChallengeStore()
    identities = InMemoryIdentityRepository()
    identities.link("firebase", "fb-uid", USER_ID)
    service = PasskeyService(
        credentials=credentials,
        challenges=challenges,
        identities=identities,
        settings=get_settings(),
    )
    return service, credentials, challenges


def _seed_credential(repo: InMemoryPasskeyCredentialRepository, sign_count: int) -> None:
    repo.add(
        PasskeyCredential(
            credential_id=CRED_ID,
            user_id=USER_ID,
            public_key=b"cose-public-key",
            sign_count=sign_count,
            transports=None,
            aaguid=None,
            backup_eligible=False,
            backup_state=False,
            device_label=None,
            created_at=utc_now(),
            last_used_at=None,
            revoked_at=None,
        )
    )


def _assertion_credential(challenge: bytes) -> dict[str, Any]:
    client_data = json.dumps(
        {
            "type": "webauthn.get",
            "challenge": bytes_to_base64url(challenge),
            "origin": "http://localhost:3000",
        }
    ).encode()
    return {
        "id": CRED_ID,
        "rawId": CRED_ID,
        "type": "public-key",
        "response": {"clientDataJSON": bytes_to_base64url(client_data)},
    }


class TestEnrolmentGate:
    def test_first_passkey_allowed_on_first_factor(self) -> None:
        service, _credentials, _challenges = _build_service()
        options = service.begin_registration(
            user_id=USER_ID, account_email="t@pablo.health", session_mfa_satisfied=False
        )
        assert "challenge" in options

    def test_second_passkey_requires_mfa_satisfied(self) -> None:
        service, credentials, _challenges = _build_service()
        _seed_credential(credentials, sign_count=0)
        with pytest.raises(PasskeyEnrollmentError):
            service.begin_registration(
                user_id=USER_ID, account_email="t@pablo.health", session_mfa_satisfied=False
            )

    def test_second_passkey_allowed_when_stepped_up(self) -> None:
        service, credentials, _challenges = _build_service()
        _seed_credential(credentials, sign_count=0)
        options = service.begin_registration(
            user_id=USER_ID, account_email="t@pablo.health", session_mfa_satisfied=True
        )
        assert "challenge" in options


class TestFinishAuthentication:
    def test_clone_detected_when_counter_not_increasing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, credentials, challenges = _build_service()
        _seed_credential(credentials, sign_count=5)
        challenges.create("authenticate", None, CHALLENGE)
        monkeypatch.setattr(
            svc,
            "verify_authentication_response",
            lambda **_: SimpleNamespace(new_sign_count=5, credential_backed_up=False),
        )
        with pytest.raises(PasskeyAssertionError):
            service.finish_authentication(credential=_assertion_credential(CHALLENGE))

    def test_unknown_challenge_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service, credentials, _challenges = _build_service()
        _seed_credential(credentials, sign_count=5)
        # No challenge was created → consume returns None before any verify.
        monkeypatch.setattr(
            svc, "verify_authentication_response", lambda **_: SimpleNamespace()
        )
        with pytest.raises(svc.PasskeyCeremonyError):
            service.finish_authentication(credential=_assertion_credential(CHALLENGE))

    def test_success_mints_token_and_advances_counter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, credentials, challenges = _build_service()
        _seed_credential(credentials, sign_count=5)
        challenges.create("authenticate", None, CHALLENGE)
        monkeypatch.setattr(
            svc,
            "verify_authentication_response",
            lambda **_: SimpleNamespace(new_sign_count=6, credential_backed_up=True),
        )
        monkeypatch.setattr(svc, "initialize_firebase_app", lambda: None)
        monkeypatch.setattr(
            svc.firebase_auth, "create_custom_token", lambda *_: b"minted-token"
        )

        result = service.finish_authentication(credential=_assertion_credential(CHALLENGE))

        assert result.custom_token == "minted-token"
        stored = credentials.get_active(CRED_ID)
        assert stored is not None
        assert stored.sign_count == 6
        assert stored.backup_state is True

    def test_mint_passes_webauthn_amr_claim(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service, credentials, challenges = _build_service()
        _seed_credential(credentials, sign_count=0)
        challenges.create("authenticate", None, CHALLENGE)
        monkeypatch.setattr(
            svc,
            "verify_authentication_response",
            lambda **_: SimpleNamespace(new_sign_count=0, credential_backed_up=False),
        )
        monkeypatch.setattr(svc, "initialize_firebase_app", lambda: None)
        captured: dict[str, Any] = {}

        def _capture(uid: str, claims: dict[str, Any]) -> bytes:
            captured["uid"] = uid
            captured["claims"] = claims
            return b"tok"

        monkeypatch.setattr(svc.firebase_auth, "create_custom_token", _capture)
        service.finish_authentication(credential=_assertion_credential(CHALLENGE))

        # 0/0 is the legitimate platform-authenticator case (not a clone).
        assert captured["uid"] == "fb-uid"
        assert captured["claims"] == {"pablo_amr": ["webauthn"]}


class TestManageCredentials:
    def test_list_returns_active_summaries(self) -> None:
        service, credentials, _challenges = _build_service()
        _seed_credential(credentials, sign_count=0)

        summaries = service.list_credentials(USER_ID)

        assert [s.credential_id for s in summaries] == [CRED_ID]
        # Summary carries metadata + label only — never key material.
        assert not hasattr(summaries[0], "public_key")

    def test_revoke_removes_credential_from_list(self) -> None:
        service, credentials, _challenges = _build_service()
        _seed_credential(credentials, sign_count=0)

        assert service.revoke_credential(user_id=USER_ID, credential_id=CRED_ID) is True
        assert service.list_credentials(USER_ID) == []
        # Revoked credential can no longer satisfy an assertion (H12).
        assert credentials.get_active(CRED_ID) is None

    def test_revoke_is_scoped_to_owner(self) -> None:
        service, credentials, _challenges = _build_service()
        _seed_credential(credentials, sign_count=0)

        other_user = "22222222-2222-4222-8222-222222222222"
        assert service.revoke_credential(user_id=other_user, credential_id=CRED_ID) is False
        # The owner's credential is untouched.
        assert [s.credential_id for s in service.list_credentials(USER_ID)] == [CRED_ID]

    def test_revoke_unknown_credential_returns_false(self) -> None:
        service, _credentials, _challenges = _build_service()
        assert service.revoke_credential(user_id=USER_ID, credential_id="nope") is False
