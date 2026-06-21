# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""SMART on FHIR standalone patient launch (authorization code + PKCE).

Implements the public-client flow Epic exposes for patient-facing apps:
discover the SMART endpoints, send the user to MyChart to authorize,
capture the redirect on a loopback callback server, and exchange the
code for an access token. No client secret is used — the PKCE
code_verifier is the proof-of-possession.
"""

import base64
import hashlib
import secrets
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from integrations.epic.auth import AccessGrant
from integrations.epic.config import EpicSettings
from integrations.epic.errors import EpicAuthError

_CALLBACK_HTML = (
    b"<!doctype html><html><head><meta charset='utf-8'>"
    b"<title>Pablo - Epic import</title></head>"
    b"<body style='font-family:sans-serif;padding:2rem'>"
    b"<h2>Authorization received</h2>"
    b"<p>You can close this tab and return to the terminal.</p>"
    b"</body></html>"
)


@dataclass(frozen=True)
class SmartConfiguration:
    """The subset of ``.well-known/smart-configuration`` we use."""

    authorization_endpoint: str
    token_endpoint: str


def generate_pkce_pair() -> tuple[str, str]:
    """Return a ``(code_verifier, code_challenge)`` PKCE pair (S256)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def discover_smart_configuration(base_url: str, client: httpx.Client) -> SmartConfiguration:
    """Fetch the SMART authorization and token endpoints for ``base_url``."""
    url = f"{base_url.rstrip('/')}/.well-known/smart-configuration"
    response = client.get(url, headers={"Accept": "application/json"})
    response.raise_for_status()
    data = response.json()
    try:
        return SmartConfiguration(
            authorization_endpoint=data["authorization_endpoint"],
            token_endpoint=data["token_endpoint"],
        )
    except KeyError as exc:  # malformed discovery document
        raise EpicAuthError(f"SMART configuration missing {exc} at {url}") from exc


class _CallbackServer(HTTPServer):
    """Single-shot loopback server that captures the OAuth redirect."""

    expected_path: str = "/callback"
    auth_code: str | None = None
    auth_state: str | None = None
    auth_error: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    """Records the authorization code/state from the redirect query."""

    server: _CallbackServer

    def do_GET(self) -> None:  # http.server dispatches on this exact name
        parsed = urlparse(self.path)
        if parsed.path != self.server.expected_path:
            self.send_response(404)
            self.end_headers()
            return
        params = parse_qs(parsed.query)
        self.server.auth_code = _first(params, "code")
        self.server.auth_state = _first(params, "state")
        self.server.auth_error = _first(params, "error")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_CALLBACK_HTML)

    def log_message(self, *args: Any) -> None:  # noqa: ARG002 - silence stderr access log
        return


def _first(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    return values[0] if values else None


class StandaloneLaunchFlow:
    """Drives the SMART standalone patient launch to a usable token."""

    def __init__(
        self, settings: EpicSettings, client: httpx.Client, *, open_browser: bool = True
    ) -> None:
        self._settings = settings
        self._client = client
        self._open_browser = open_browser

    def acquire(self) -> AccessGrant:
        """Run the full flow and return the access token for the patient."""
        smart = discover_smart_configuration(self._settings.fhir_base_url, self._client)
        verifier, challenge = generate_pkce_pair()
        state = secrets.token_urlsafe(32)
        authorize_url = self._build_authorize_url(smart.authorization_endpoint, challenge, state)

        print("\nOpen this URL and sign in to MyChart to authorize the import:\n")
        print(f"  {authorize_url}\n")
        if self._open_browser:
            webbrowser.open(authorize_url)

        code = self._await_callback(state)
        return self._exchange_code(smart.token_endpoint, code, verifier)

    def _build_authorize_url(self, endpoint: str, challenge: str, state: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._settings.client_id,
                "redirect_uri": self._settings.redirect_uri,
                "scope": self._settings.scopes,
                "state": state,
                # Epic requires the FHIR base as the audience.
                "aud": self._settings.fhir_base_url,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{endpoint}?{query}"

    def _await_callback(self, expected_state: str) -> str:
        server = _CallbackServer(
            (self._settings.redirect_host, self._settings.redirect_port),
            _CallbackHandler,
        )
        server.expected_path = self._settings.redirect_path
        server.timeout = 1.0
        deadline = time.monotonic() + self._settings.callback_timeout
        try:
            print(f"Waiting for the MyChart redirect on {self._settings.redirect_uri} ...")
            while server.auth_code is None and server.auth_error is None:
                if time.monotonic() > deadline:
                    raise EpicAuthError("Timed out waiting for the MyChart authorization redirect.")
                server.handle_request()
        finally:
            server.server_close()

        if server.auth_error is not None:
            raise EpicAuthError(
                f"Authorization denied by the identity provider: {server.auth_error}"
            )
        if server.auth_state != expected_state:
            raise EpicAuthError("State mismatch on the OAuth redirect — possible CSRF, aborting.")
        if server.auth_code is None:  # defensive: loop only exits with code or error
            raise EpicAuthError("No authorization code returned on the redirect.")
        return server.auth_code

    def _exchange_code(self, token_endpoint: str, code: str, verifier: str) -> AccessGrant:
        response = self._client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._settings.redirect_uri,
                "client_id": self._settings.client_id,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != httpx.codes.OK:
            raise EpicAuthError(
                f"Token exchange failed ({response.status_code}): {response.text}"
            )
        payload = response.json()
        return AccessGrant(
            access_token=payload["access_token"],
            patient_id=payload.get("patient"),
            scope=payload.get("scope", ""),
            expires_in=int(payload.get("expires_in", 0)),
            refresh_token=payload.get("refresh_token"),
            raw=payload,
        )
