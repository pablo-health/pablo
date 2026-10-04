# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The code-for-token exchange accepts Google's wider answer to an added permission.

These run the real ``google_auth_oauthlib`` flow and the real ``oauthlib`` token
parsing; only the HTTP call to Google's token endpoint is replaced. The other
connect tests replace the whole flow, which is how an exchange that raised on
every "Scan calendar" reached production: asked for read access alone with
``include_granted_scopes``, Google answers with every permission the account
has granted the app, and oauthlib refused the difference as "Scope has
changed".
"""

from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import patch

import pytest
from app.services.google_calendar_service import (
    CalendarScopeNotGrantedError,
    _build_flow,
    _exchange_code,
)
from requests import Request, Response

READONLY = "https://www.googleapis.com/auth/calendar.readonly"
FREEBUSY = "https://www.googleapis.com/auth/calendar.freebusy"
APP_CREATED = "https://www.googleapis.com/auth/calendar.app.created"


def _token_response(scope: str | None) -> Response:
    body: dict[str, Any] = {
        "access_token": "ya29.test",
        "expires_in": 3599,
        "refresh_token": "1//test",
        "token_type": "Bearer",
    }
    if scope is not None:
        body["scope"] = scope
    response = Response()
    response.status_code = 200
    response._content = json.dumps(body).encode()
    response.headers["Content-Type"] = "application/json"
    # requests_oauthlib logs the request it made; a real Response carries it.
    response.request = Request("POST", "https://oauth2.googleapis.com/token").prepare()
    return response


def _exchange(requested: list[str], google_scope: str | None) -> Any:
    flow = _build_flow("client-id", "client-secret", "https://app.test/cb", requested)
    flow.code_verifier = "v" * 43
    with patch(
        "requests_oauthlib.OAuth2Session.request", return_value=_token_response(google_scope)
    ):
        _exchange_code(flow, "auth-code", requested)
    return flow


def test_an_added_permission_accepts_the_wider_grant_google_returns() -> None:
    """The production failure: asked for read access, answered with all three."""
    flow = _exchange([READONLY], f"{READONLY} {FREEBUSY} {APP_CREATED}")

    assert flow.credentials.token == "ya29.test"
    assert flow.credentials.refresh_token == "1//test"
    # The session's own scope is back, so credentials carry what was asked for.
    assert flow.oauth2session.scope == [READONLY]


def test_an_exact_answer_still_exchanges() -> None:
    flow = _exchange([APP_CREATED, FREEBUSY], f"{FREEBUSY} {APP_CREATED}")
    assert flow.credentials.token == "ya29.test"


def test_a_permission_unticked_on_googles_screen_is_refused() -> None:
    with pytest.raises(CalendarScopeNotGrantedError, match=re.escape(READONLY)):
        _exchange([READONLY], f"{FREEBUSY} {APP_CREATED}")


def test_an_answer_without_scopes_is_taken_as_what_was_asked() -> None:
    flow = _exchange([READONLY], None)
    assert flow.credentials.token == "ya29.test"
