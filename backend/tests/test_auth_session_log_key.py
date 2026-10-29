# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""One sign-in, one opaque id on every access line it produces."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import jwt
from app.auth import idle_session
from app.auth import service as auth_service
from app.auth.providers import VerifiedIdentity
from app.logging_config import begin_request_log_identity, end_request_log_identity

UID = "firebase-uid-7"
SIGNED_IN_AT = 1_760_000_000


def test_same_sign_in_same_key_across_refreshes() -> None:
    first = idle_session.session_log_key({"uid": UID, "auth_time": SIGNED_IN_AT, "iat": 1})
    refreshed = idle_session.session_log_key({"uid": UID, "auth_time": SIGNED_IN_AT, "iat": 2})
    assert first == refreshed
    assert first is not None
    assert len(first) == 16
    assert all(c in "0123456789abcdef" for c in first)


def test_new_sign_in_new_key() -> None:
    before = idle_session.session_log_key({"uid": UID, "auth_time": SIGNED_IN_AT})
    after = idle_session.session_log_key({"uid": UID, "auth_time": SIGNED_IN_AT + 60})
    assert before != after


def test_key_reveals_neither_the_uid_nor_the_sign_in_time() -> None:
    key = idle_session.session_log_key({"uid": UID, "auth_time": SIGNED_IN_AT})
    assert key is not None
    assert UID not in key
    assert str(SIGNED_IN_AT) not in key


def test_uses_the_same_pair_the_idle_markers_are_keyed_on() -> None:
    claims = {"sub": UID, "auth_time": SIGNED_IN_AT}
    subject, auth_time = idle_session._session_identifiers(claims)
    assert idle_session._session_marker_key(subject, auth_time).endswith(f"{UID}:{SIGNED_IN_AT}")
    assert idle_session.session_log_key(claims) == idle_session.session_log_key(
        {"uid": UID, "auth_time": SIGNED_IN_AT}
    )


def test_token_without_the_pair_has_no_key_and_does_not_raise() -> None:
    assert idle_session.session_log_key({"uid": UID}) is None
    assert idle_session.session_log_key({"auth_time": SIGNED_IN_AT}) is None


def _verified() -> VerifiedIdentity:
    return VerifiedIdentity(
        provider="firebase",
        subject_id=UID,
        email="clinician@example.com",
        email_verified=True,
        mfa_satisfied=True,
        claims={"uid": UID, "sub": UID, "auth_time": SIGNED_IN_AT},
    )


def test_verified_request_binds_auth_session_on_the_stashed_path() -> None:
    identity = _verified()
    request = MagicMock()
    request.state.verified_identity = identity
    request.state.verified_identity_token = "tok"
    holder, token = begin_request_log_identity()
    try:
        auth_service._verify_request_identity(request, "tok")
    finally:
        end_request_log_identity(token)

    assert holder.auth_session == idle_session.session_log_key(identity.claims)
    assert holder.claimed_auth_session is None


def test_expired_token_groups_with_the_sign_in_it_came_from() -> None:
    expired = jwt.encode(
        {"sub": UID, "auth_time": SIGNED_IN_AT, "exp": 1}, "k" * 32, algorithm="HS256"
    )
    repo = MagicMock()
    repo.get_user_id.return_value = "user-1"
    holder, token = begin_request_log_identity()
    try:
        with patch.object(auth_service, "get_identity_repository", return_value=repo):
            auth_service._note_expired_token_subject(expired)
    finally:
        end_request_log_identity(token)

    assert holder.claimed_auth_session == idle_session.session_log_key(_verified().claims)
    assert holder.auth_session is None
