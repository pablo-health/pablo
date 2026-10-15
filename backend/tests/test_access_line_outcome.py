# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The access line says how a failed request failed, and who it was for.

Each test drives a real request through ``RequestContextMiddleware`` and the
registered exception handlers, and reads the ``request_completed`` record the
real ``JSONFormatter`` writes, so the assertions are about the JSON an operator
filters on. Responses are checked too: recording an outcome must not change
what the client receives.
"""

from __future__ import annotations

import io
import json
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import jwt
import pytest
from app.api_errors import BadRequestError, register_exception_handlers
from app.auth import service as auth_service
from app.logging_config import (
    JSONFormatter,
    RedactPHIFilter,
    begin_request_log_identity,
    bind_log_user_id,
    end_request_log_identity,
    request_id_var,
    route_template_var,
    tenant_id_var,
    user_id_var,
)
from app.middleware.request_context import RequestContextMiddleware
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.testclient import TestClient
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator

# A value that must never reach a log record, whatever the failure path.
SENTINEL = "Zebulon Quartermaine 555-0142"


@pytest.fixture(autouse=True)
def _reset_contextvars() -> Generator[None]:
    yield
    request_id_var.set(None)
    user_id_var.set(None)
    tenant_id_var.set(None)
    route_template_var.set(None)


@contextmanager
def _access_lines() -> Iterator[io.StringIO]:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(RedactPHIFilter())
    lg = logging.getLogger("pablo.access")
    saved = (lg.handlers, lg.level, lg.propagate)
    lg.handlers = [handler]
    lg.setLevel(logging.DEBUG)
    lg.propagate = False
    try:
        yield buf
    finally:
        lg.handlers, lg.level, lg.propagate = saved


def _only_record(buf: io.StringIO) -> dict[str, Any]:
    (line,) = [line for line in buf.getvalue().splitlines() if line.strip()]
    record: dict[str, Any] = json.loads(line)
    assert record["event"] == "request_completed"
    return record


class _Visit(BaseModel):
    place_of_service: str
    inputs: dict[str, int]


def _sync_user(user_id: str) -> Any:
    def dependency() -> None:
        bind_log_user_id(user_id)

    return dependency


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    bearer = HTTPBearer()

    @app.post("/notes", dependencies=[Depends(_sync_user("user-42"))])
    def create_note() -> None:
        raise BadRequestError(f"bad input {SENTINEL}", code="INVALID_NOTE_INPUTS")

    @app.get("/missing")
    def missing() -> None:
        raise HTTPException(status_code=404, detail=f"Patient {SENTINEL} not found")

    @app.get("/gated", dependencies=[Depends(_sync_user("user-42"))])
    def gated() -> None:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "FEATURE_NOT_ENABLED", "message": "no", "details": {}}},
        )

    @app.post("/visit")
    def visit(body: _Visit) -> dict[str, str]:
        return {"ok": body.place_of_service}

    @app.get("/boom", dependencies=[Depends(_sync_user("user-42"))])
    def boom() -> None:
        raise RuntimeError(SENTINEL)

    @app.get("/bearer")
    def needs_bearer(_c: HTTPAuthorizationCredentials = Depends(bearer)) -> None:
        return None

    @app.get("/expired")
    def expired() -> None:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "TOKEN_EXPIRED", "message": "expired", "details": {}}},
        )

    @app.get("/fine", dependencies=[Depends(_sync_user("user-42"))])
    def fine() -> dict[str, str]:
        return {"ok": "true"}

    return app


class TestErrorCode:
    def test_api_error_records_its_code_and_who_hit_it(self) -> None:
        with _access_lines() as buf:
            response = TestClient(_build_app()).post("/notes")

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_NOTE_INPUTS"
        record = _only_record(buf)
        assert record["error_code"] == "INVALID_NOTE_INPUTS"
        assert record["user_id"] == "user-42"
        assert "auth_failure" not in record
        assert SENTINEL not in json.dumps(record)

    def test_plain_string_detail_is_logged_as_its_status_never_as_text(self) -> None:
        with _access_lines() as buf:
            response = TestClient(_build_app()).get("/missing")

        assert response.status_code == 404
        assert response.json() == {"detail": f"Patient {SENTINEL} not found"}
        record = _only_record(buf)
        assert record["error_code"] == "HTTP_404"
        assert SENTINEL not in json.dumps(record)

    def test_envelope_code_on_a_refusal_is_kept_with_the_caller(self) -> None:
        with _access_lines() as buf:
            TestClient(_build_app()).get("/gated")

        record = _only_record(buf)
        assert record["status_code"] == 403
        assert record["error_code"] == "FEATURE_NOT_ENABLED"
        assert record["user_id"] == "user-42"

    def test_success_carries_no_outcome_fields(self) -> None:
        with _access_lines() as buf:
            TestClient(_build_app()).get("/fine")

        record = _only_record(buf)
        for key in ("error_code", "error_fields", "error_type", "auth_failure"):
            assert key not in record


class TestValidationFailure:
    def test_names_the_fields_but_never_the_values(self) -> None:
        payload = {"inputs": {SENTINEL: "not-a-number", "visits": "x"}}
        with _access_lines() as buf:
            response = TestClient(_build_app()).post("/visit", json=payload)

        assert response.status_code == 422
        record = _only_record(buf)
        assert record["error_code"] == "VALIDATION_ERROR"
        assert "body.place_of_service" in record["error_fields"]
        assert "body.inputs.visits" in record["error_fields"]
        # A client-chosen dict key is free text; it is masked, not logged.
        assert "body.inputs.*" in record["error_fields"]
        assert SENTINEL not in json.dumps(record)
        assert "not-a-number" not in json.dumps(record)

    def test_response_is_fastapis_own_422(self) -> None:
        bare = FastAPI()

        @bare.post("/visit")
        def visit(body: _Visit) -> dict[str, str]:
            return {"ok": body.place_of_service}

        payload = {"inputs": {"a": "x"}}
        ours = TestClient(_build_app()).post("/visit", json=payload)
        theirs = TestClient(bare).post("/visit", json=payload)
        assert ours.status_code == theirs.status_code == 422
        assert ours.json() == theirs.json()


class TestUnhandled:
    def test_records_internal_error_and_the_exception_class(self) -> None:
        with _access_lines() as buf:
            response = TestClient(_build_app(), raise_server_exceptions=False).get("/boom")

        assert response.status_code == 500
        record = _only_record(buf)
        assert record["error_code"] == "INTERNAL_ERROR"
        assert record["error_type"] == "RuntimeError"
        assert record["user_id"] == "user-42"
        assert SENTINEL not in json.dumps(record)


class TestAuthFailure:
    def test_no_credential_is_named_as_such(self) -> None:
        with _access_lines() as buf:
            response = TestClient(_build_app()).get("/bearer")

        assert response.status_code == 401
        record = _only_record(buf)
        assert record["auth_failure"] == "NO_CREDENTIALS"
        assert record["error_code"] == "NO_CREDENTIALS"

    def test_expired_token_reason_is_recorded(self) -> None:
        with _access_lines() as buf:
            TestClient(_build_app()).get("/expired")

        record = _only_record(buf)
        assert record["auth_failure"] == "TOKEN_EXPIRED"
        assert record["error_code"] == "TOKEN_EXPIRED"
        assert "user_id" not in record


class TestClaimedUserOnExpiredToken:
    def _expired_token(self, subject: str) -> str:
        return jwt.encode({"sub": subject, "exp": 1}, "k" * 32, algorithm="HS256")

    def test_expired_token_names_the_mapped_user_as_claimed_only(self) -> None:
        repo = MagicMock()
        repo.get_user_id.return_value = "user-42"
        identity, token = begin_request_log_identity()
        try:
            with patch.object(auth_service, "get_identity_repository", return_value=repo):
                auth_service._note_expired_token_subject(self._expired_token("firebase-uid-1"))
        finally:
            end_request_log_identity(token)

        repo.get_user_id.assert_called_once_with("firebase", "firebase-uid-1")
        assert identity.claimed_user_id == "user-42"
        assert identity.user_id is None

    def test_unmapped_or_garbage_token_names_no_one_and_never_raises(self) -> None:
        repo = MagicMock()
        repo.get_user_id.return_value = None
        identity, token = begin_request_log_identity()
        try:
            with patch.object(auth_service, "get_identity_repository", return_value=repo):
                auth_service._note_expired_token_subject(self._expired_token("unknown"))
                auth_service._note_expired_token_subject("not-a-jwt")
        finally:
            end_request_log_identity(token)

        assert identity.claimed_user_id is None

    def test_firebase_expiry_branch_notes_the_subject(self) -> None:
        firebase_expired = auth_service.firebase_auth.ExpiredIdTokenError("expired", cause=None)
        with (
            patch.object(auth_service, "initialize_firebase_app"),
            patch.object(
                auth_service.firebase_auth, "verify_id_token", side_effect=firebase_expired
            ),
            patch.object(auth_service, "_note_expired_token_subject") as noted,
            pytest.raises(HTTPException) as raised,
        ):
            auth_service.verify_firebase_token("tok")

        assert raised.value.status_code == 401
        noted.assert_called_once_with("tok")


class TestSessionPeekIsAttributed:
    def test_session_peek_binds_the_mapped_user(self) -> None:
        verified = MagicMock(provider="firebase", subject_id="firebase-uid-1", claims={"a": 1})
        repo = MagicMock()
        repo.get_user_id.return_value = "user-42"
        identity, token = begin_request_log_identity()
        try:
            with (
                patch.object(auth_service, "_verify_request_identity", return_value=verified),
                patch.object(auth_service, "get_identity_repository", return_value=repo),
            ):
                claims = auth_service.get_session_peek_claims(
                    MagicMock(), HTTPAuthorizationCredentials(scheme="Bearer", credentials="t")
                )
        finally:
            end_request_log_identity(token)

        assert claims == {"a": 1}
        assert identity.user_id == "user-42"

    def test_session_peek_still_answers_when_the_lookup_fails(self) -> None:
        verified = MagicMock(provider="firebase", subject_id="s", claims={"a": 1})
        repo = MagicMock()
        repo.get_user_id.side_effect = RuntimeError("db down")
        with (
            patch.object(auth_service, "_verify_request_identity", return_value=verified),
            patch.object(auth_service, "get_identity_repository", return_value=repo),
        ):
            claims = auth_service.get_session_peek_claims(
                MagicMock(), HTTPAuthorizationCredentials(scheme="Bearer", credentials="t")
            )

        assert claims == {"a": 1}
