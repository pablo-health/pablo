# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Client classification for the access line: provided first, derived otherwise.

The User-Agent strings below were captured from the backend's own request
logs (production and the development deployment, 2026-10-08 to 2026-10-15),
not written by hand — a User-Agent is not sensitive, and a guessed one only
confirms the guess. Where a class had no captured example the test says so.
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any

import pytest
from app.api_errors import register_exception_handlers
from app.logging_config import JSONFormatter
from app.middleware.client_info import classify_client
from app.middleware.request_context import RequestContextMiddleware
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

# --- captured -----------------------------------------------------------
MAC_SAFARI = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/26.2 Safari/605.1.15"
)
MAC_CHROME = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)
MAC_FIREFOX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0"
WINDOWS_EDGE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0"
)
WINDOWS_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0.0.0 Safari/537.36"
)
IPHONE_SAFARI = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1"
)
IPHONE_CHROME = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 26_3_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) CriOS/144.0.7559.95 Mobile/15E148 Safari/604.1"
)
LINUX_HEADLESS = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "HeadlessChrome/153.0.8010.12 Safari/537.36"
)
RECORDING_APP_MAC = "Pablo/24 CFNetwork/3860.300.31 Darwin/25.2.0"
TEST_HARNESS = "practice-harness (unknown version) CFNetwork/3826.600.41.1.1 Darwin/24.6.0"
FRONTEND_SERVER = "node"
CLOUD_TASKS = "Google-Cloud-Tasks"
CLOUD_SCHEDULER = "Google-Cloud-Scheduler"
UPTIME = "GoogleStackdriverMonitoring-UptimeChecks(https://cloud.google.com/monitoring)"
CRAWLER = (
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-SearchBot/1.0; "
    "+claudebot@anthropic.com)"
)
SCANNER = "Mozilla/5.0 (l9scan/2.0.3373e2533323e24353e24333; +https://leakix.net)"
CURL = "curl/8.5.0"
# --- end captured ---------------------------------------------------------


def _classify(**headers: str) -> Any:
    return classify_client(Headers({k.replace("_", "-"): v for k, v in headers.items()}))


@pytest.mark.parametrize(
    ("ua", "app", "device", "browser"),
    [
        (MAC_SAFARI, "browser", "mac", "safari"),
        (MAC_CHROME, "browser", "mac", "chrome"),
        (MAC_FIREFOX, "browser", "mac", "firefox"),
        (WINDOWS_EDGE, "browser", "windows", "edge"),
        (WINDOWS_CHROME, "browser", "windows", "chrome"),
        (IPHONE_SAFARI, "browser", "iphone", "safari"),
        (IPHONE_CHROME, "browser", "iphone", "chrome"),
        (LINUX_HEADLESS, "browser", "linux", "chrome"),
        (RECORDING_APP_MAC, "recording_app", "mac", "other"),
        (TEST_HARNESS, "test_harness", "mac", "other"),
        (FRONTEND_SERVER, "frontend_server", "other", "other"),
        (CLOUD_TASKS, "scheduler", "other", "other"),
        (CLOUD_SCHEDULER, "scheduler", "other", "other"),
        (UPTIME, "monitor", "other", "other"),
        (CRAWLER, "bot", "other", "other"),
        (SCANNER, "bot", "other", "other"),
        (CURL, "other", "other", "other"),
    ],
)
def test_captured_user_agents_classify(ua: str, app: str, device: str, browser: str) -> None:
    info = _classify(user_agent=ua)
    assert (info.app, info.device, info.browser) == (app, device, browser)
    assert info.source == "derived"


def test_recording_app_build_number_becomes_its_version() -> None:
    assert _classify(user_agent=RECORDING_APP_MAC).version == "24"
    assert _classify(user_agent=MAC_SAFARI).version is None


def test_install_id_marks_the_recording_app_even_with_no_user_agent() -> None:
    # The Windows build's HTTP client sends no User-Agent; its device-binding
    # header is what every build already sends.
    info = _classify(x_install_id="install-1")
    assert (info.app, info.device) == ("recording_app", "windows")
    assert _classify(user_agent=RECORDING_APP_MAC, dpop="proof").device == "mac"


@pytest.mark.parametrize("ua", [None, "", "   ", "\x00garbage\x7f", "a" * 5000])
def test_missing_or_garbage_user_agent_is_other_and_never_raises(ua: str | None) -> None:
    info = _classify(user_agent=ua) if ua is not None else _classify()
    assert (info.app, info.device, info.browser) == ("other", "other", "other")
    assert info.source == "derived"
    assert info.version is None
    assert info.build is None


def test_provided_values_win_over_the_user_agent() -> None:
    info = _classify(
        user_agent=MAC_SAFARI,
        x_client_app="recording_app",
        x_client_device="windows",
        x_client_browser="edge",
        x_client_version="3.1.0",
        x_client_build="abc1234",
    )
    assert (info.app, info.device, info.browser) == ("recording_app", "windows", "edge")
    assert info.version == "3.1.0"
    assert info.build == "abc1234"
    assert info.source == "header"


def test_each_field_resolves_on_its_own() -> None:
    info = _classify(user_agent=WINDOWS_CHROME, x_client_device="MAC")
    assert info.device == "mac"
    assert (info.app, info.browser) == ("browser", "chrome")
    assert info.source == "mixed"


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("x_client_app", "toaster"),
        ("x_client_device", "<script>"),
        ("x_client_browser", "netscape"),
        ("x_client_version", "v" * 200),
        ("x_client_version", "1.0 <img>"),
        ("x_client_build", "<script>alert(1)</script>"),
        ("x_client_build", "XYZ1234"),
        ("x_client_build", "a" * 41),
    ],
)
def test_invalid_provided_values_fall_back_to_derivation(header: str, value: str) -> None:
    info = _classify(user_agent=MAC_SAFARI, **{header: value})
    assert (info.app, info.device, info.browser) == ("browser", "mac", "safari")
    assert info.source == "derived"
    assert value not in json.dumps(info.log_fields())


def test_no_headers_matches_derive_only() -> None:
    plain = _classify(user_agent=MAC_CHROME)
    assert plain.log_fields() == {
        "client_app": "browser",
        "client_device": "mac",
        "client_browser": "chrome",
        "client_info_source": "derived",
    }


def test_access_line_carries_the_fields_but_not_the_raw_user_agent() -> None:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"ok": "true"}

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JSONFormatter())
    lg = logging.getLogger("pablo.access")
    saved = (lg.handlers, lg.level, lg.propagate)
    lg.handlers, lg.propagate = [handler], False
    lg.setLevel(logging.DEBUG)
    try:
        response = TestClient(app).get(
            "/ping", headers={"User-Agent": RECORDING_APP_MAC, "X-Client-Build": "deadbee"}
        )
    finally:
        lg.handlers, lg.level, lg.propagate = saved

    assert response.status_code == 200
    (record,) = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    assert record["client_app"] == "recording_app"
    assert record["client_device"] == "mac"
    assert record["client_version"] == "24"
    assert record["client_build"] == "deadbee"
    assert record["client_info_source"] == "derived"
    assert RECORDING_APP_MAC not in json.dumps(record)
