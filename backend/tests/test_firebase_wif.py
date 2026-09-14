# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the Workload Identity Federation credential.

The HTTP exchange is stubbed with a small in-process transport (no network):
one fake ``request`` callable routes to the ECS credentials endpoint, the STS
token endpoint, and the IAM impersonation endpoint by URL, mirroring exactly
what ``google.auth`` calls during ``refresh()``.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.auth.firebase_wif import WorkloadIdentityCredential, _EcsAwsSupplier
from google.auth import exceptions as google_auth_exceptions

AUDIENCE = (
    "//iam.googleapis.com/projects/123456789/locations/global/"
    "workloadIdentityPools/pablo-pool/providers/pablo-provider"
)
IMPERSONATION_URL = (
    "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
    "wif-runner@my-project.iam.gserviceaccount.com:generateAccessToken"
)
STS_URL = "https://sts.googleapis.com/v1/token"
ECS_CREDS_URL = "http://169.254.170.2/v1/creds"


@dataclass
class _FakeResponse:
    status: int
    data: bytes
    headers: dict[str, str] = field(default_factory=dict)


class _FakeTransport:
    """Routes google.auth's HTTP calls by URL; records every call made."""

    def __init__(
        self,
        *,
        sts_status: int = 200,
        sts_token: str = "federated-token",  # noqa: S107 (fake test value, not a secret)
    ) -> None:
        self.sts_status = sts_status
        self.sts_token = sts_token
        self.calls: list[dict[str, Any]] = []
        self._iam_call_count = 0

    def __call__(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        **_kwargs: Any,
    ) -> _FakeResponse:
        self.calls.append({"url": url, "method": method, "headers": headers, "body": body})

        if url == ECS_CREDS_URL:
            return _FakeResponse(
                status=200,
                data=json.dumps(
                    {
                        "AccessKeyId": "AKIAFAKE",
                        "SecretAccessKey": "fake-secret",
                        "Token": "fake-session-token",
                    }
                ).encode(),
            )

        if url == STS_URL:
            if self.sts_status != 200:
                return _FakeResponse(
                    status=self.sts_status,
                    data=json.dumps(
                        {"error": "invalid_grant", "error_description": "STS exchange refused"}
                    ).encode(),
                )
            # Impersonation is in play, so this exchange is for the *source*
            # (federated) token, which google-auth scopes to the IAM API
            # regardless of the caller's requested scopes.
            parsed_body = urllib.parse.parse_qs((body or b"").decode())
            assert parsed_body["audience"] == [AUDIENCE]
            assert parsed_body["scope"] == ["https://www.googleapis.com/auth/iam"]
            return _FakeResponse(
                status=200,
                data=json.dumps(
                    {
                        "access_token": self.sts_token,
                        "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    }
                ).encode(),
            )

        if url == IMPERSONATION_URL:
            self._iam_call_count += 1
            iam_body = json.loads((body or b"").decode())
            assert iam_body["scope"] == ["https://www.googleapis.com/auth/cloud-platform"]
            expire_time = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
            return _FakeResponse(
                status=200,
                data=json.dumps(
                    {
                        "accessToken": f"impersonated-token-{self._iam_call_count}",
                        "expireTime": expire_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                ).encode(),
            )

        raise AssertionError(f"unexpected URL requested in test: {url}")


@pytest.fixture(autouse=True)
def _ecs_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", ECS_CREDS_URL)
    monkeypatch.setenv("AWS_REGION", "us-east-1")


class TestWorkloadIdentityCredentialValidation:
    def test_rejects_malformed_audience(self) -> None:
        with pytest.raises(ValueError, match="firebase_wif_audience"):
            WorkloadIdentityCredential(
                audience="not-a-valid-audience",
                sa_impersonation_url=IMPERSONATION_URL,
                project_id="my-project",
            )

    def test_rejects_malformed_impersonation_url(self) -> None:
        with pytest.raises(ValueError, match="firebase_wif_sa_impersonation_url"):
            WorkloadIdentityCredential(
                audience=AUDIENCE,
                sa_impersonation_url="https://example.com/not-iam",
                project_id="my-project",
            )

    def test_extracts_service_account_email_and_project_id(self) -> None:
        cred = WorkloadIdentityCredential(
            audience=AUDIENCE,
            sa_impersonation_url=IMPERSONATION_URL,
            project_id="my-project",
        )
        assert cred.service_account_email == "wif-runner@my-project.iam.gserviceaccount.com"
        assert cred.project_id == "my-project"


class TestWorkloadIdentityCredentialExchange:
    def _build(self) -> WorkloadIdentityCredential:
        return WorkloadIdentityCredential(
            audience=AUDIENCE,
            sa_impersonation_url=IMPERSONATION_URL,
            project_id="my-project",
        )

    def test_success_returns_token_exchanged_for_expected_audience_and_scope(self) -> None:
        cred = self._build()
        google_cred = cred.get_credential()
        transport = _FakeTransport()

        google_cred.refresh(transport)

        assert google_cred.token == "impersonated-token-1"
        assert google_cred.expiry is not None
        assert not google_cred.expired
        sts_calls = [c for c in transport.calls if c["url"] == STS_URL]
        assert len(sts_calls) == 1

    def test_sts_refusal_raises_typed_error_and_does_not_cache_a_token(self) -> None:
        cred = self._build()
        google_cred = cred.get_credential()
        transport = _FakeTransport(sts_status=400)

        with pytest.raises(google_auth_exceptions.OAuthError):
            google_cred.refresh(transport)

        assert google_cred.token is None

    def test_expired_cached_token_triggers_reexchange(self) -> None:
        cred = self._build()
        google_cred = cred.get_credential()
        transport = _FakeTransport()

        google_cred.refresh(transport)
        first_token = google_cred.token
        assert not google_cred.expired

        # Force the cached token to look expired, as if time had passed.
        google_cred.expiry = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
        assert google_cred.expired

        google_cred.refresh(transport)

        assert google_cred.token != first_token
        assert not google_cred.expired
        iam_calls = [c for c in transport.calls if c["url"] == IMPERSONATION_URL]
        assert len(iam_calls) == 2


class TestEcsAwsSupplier:
    def test_reads_credentials_from_full_uri(self) -> None:
        transport = _FakeTransport()
        supplier = _EcsAwsSupplier()

        creds = supplier.get_aws_security_credentials(None, transport)

        assert creds.access_key_id == "AKIAFAKE"
        assert creds.secret_access_key == "fake-secret"
        assert creds.session_token == "fake-session-token"

    def test_missing_uri_raises_refresh_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", raising=False)
        monkeypatch.delenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", raising=False)
        supplier = _EcsAwsSupplier()

        with pytest.raises(google_auth_exceptions.RefreshError, match="ECS"):
            supplier.get_aws_security_credentials(None, _FakeTransport())

    def test_non_200_response_raises_refresh_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def failing_transport(url: str, method: str = "GET", **_kwargs: Any) -> _FakeResponse:
            return _FakeResponse(status=403, data=b"denied")

        supplier = _EcsAwsSupplier()

        with pytest.raises(google_auth_exceptions.RefreshError, match="403"):
            supplier.get_aws_security_credentials(None, failing_transport)

    def test_region_read_from_env(self) -> None:
        supplier = _EcsAwsSupplier()
        assert supplier.get_aws_region(None, None) == "us-east-1"

    def test_region_missing_raises_refresh_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        supplier = _EcsAwsSupplier()

        with pytest.raises(google_auth_exceptions.RefreshError, match="region"):
            supplier.get_aws_region(None, None)
