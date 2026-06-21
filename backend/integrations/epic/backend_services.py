# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Headless SMART Backend Services auth (OAuth2 client-credentials).

The app proves its identity with a JWT signed by a registered private
key (``private_key_jwt``) instead of an interactive login, and receives a
short-lived system-level access token. There is no patient context, so
callers name the patient to pull explicitly. This is the flow Pablo would
use for server-side, multi-patient sync once a practice's Epic org has
onboarded the app.
"""

import time
import uuid
from pathlib import Path

import httpx
import jwt

from integrations.epic.auth import AccessGrant
from integrations.epic.config import EpicSettings
from integrations.epic.errors import EpicAuthError, EpicConfigError
from integrations.epic.smart_auth import discover_smart_configuration

# SMART Backend Services mandates an asymmetric signature; Epic accepts RS384.
_SIGNING_ALGORITHM = "RS384"
_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


class BackendServicesAuth:
    """Acquires a system-level access token via signed JWT client assertion."""

    def __init__(self, settings: EpicSettings, client: httpx.Client) -> None:
        self._settings = settings
        self._client = client

    def acquire(self) -> AccessGrant:
        private_key = self._load_private_key()
        smart = discover_smart_configuration(self._settings.fhir_base_url, self._client)
        assertion = self._build_client_assertion(smart.token_endpoint, private_key)

        response = self._client.post(
            smart.token_endpoint,
            data={
                "grant_type": "client_credentials",
                "client_assertion_type": _ASSERTION_TYPE,
                "client_assertion": assertion,
                "scope": self._settings.backend_scopes,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != httpx.codes.OK:
            raise EpicAuthError(
                f"Backend Services token request failed ({response.status_code}): {response.text}"
            )
        payload = response.json()
        return AccessGrant(
            access_token=payload["access_token"],
            patient_id=None,  # system scope: no patient context
            scope=payload.get("scope", ""),
            expires_in=int(payload.get("expires_in", 0)),
            refresh_token=None,
            raw=payload,
        )

    def _load_private_key(self) -> str:
        path = self._settings.backend_private_key_path
        if path is None or not self._settings.backend_kid:
            raise EpicConfigError(
                "Backend mode needs EPIC_BACKEND_PRIVATE_KEY_PATH (PEM) and EPIC_BACKEND_KID."
            )
        if not self._settings.client_id:
            raise EpicConfigError("Backend mode needs EPIC_CLIENT_ID (the backend app's id).")
        return Path(path).read_text(encoding="utf-8")

    def _build_client_assertion(self, token_endpoint: str, private_key: str) -> str:
        now = int(time.time())
        claims = {
            "iss": self._settings.client_id,
            "sub": self._settings.client_id,
            "aud": token_endpoint,
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": now + self._settings.jwt_assertion_ttl,
        }
        return jwt.encode(
            claims,
            private_key,
            algorithm=_SIGNING_ALGORITHM,
            headers={"kid": self._settings.backend_kid, "typ": "JWT"},
        )
