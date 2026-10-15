# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""CORS preflight for the optional X-Client-* request headers.

A browser asks before it sends a header the API has not listed, and a refused
preflight fails the real request outright. So when a frontend on another
origin starts sending one of these headers, the API's CORS list is the only
thing standing between an ordinary deployment and every request failing.
These run the preflight through the real application, not a copy of the list.
"""

from __future__ import annotations

import pytest
from app.main import app, cors_origins
from fastapi.testclient import TestClient

CLIENT_HEADERS = [
    "X-Client-Build",
    "X-Client-App",
    "X-Client-Device",
    "X-Client-Browser",
    "X-Client-Version",
]


def _preflight(request_headers: str) -> tuple[int, str]:
    response = TestClient(app).options(
        "/api/patients",
        headers={
            "Origin": cors_origins[0],
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": request_headers,
        },
    )
    return response.status_code, response.headers.get("access-control-allow-headers", "")


@pytest.mark.parametrize("header", CLIENT_HEADERS)
def test_preflight_allows_client_header(header: str) -> None:
    status, allowed = _preflight(f"authorization, content-type, {header.lower()}")

    assert status == 200
    assert header.lower() in allowed.lower()


def test_preflight_still_refuses_an_unlisted_header() -> None:
    # Guards the test itself: if the middleware allowed everything, the cases
    # above would pass without the list doing any work.
    status, _ = _preflight("x-not-a-pablo-header")

    assert status == 400
