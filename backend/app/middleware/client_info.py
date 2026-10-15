# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What kind of client sent a request, for the access line.

A client may say what it is in ``X-Client-App``, ``X-Client-Device``,
``X-Client-Browser``, ``X-Client-Version`` and ``X-Client-Build``. Each one is
optional and decided on its own: a valid header wins, and anything missing or
malformed is derived from what every client already sends (the User-Agent and,
for the recording app, its device-binding headers). Nothing here is required
of a client, nothing here changes a response, and none of it is used for any
decision — the values are self-reported and only ever logged.

Every output is a fixed enum value or a short validated token. The raw
User-Agent is not repeated here; the platform request log already has it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

APPS = frozenset(
    {
        "browser",
        "recording_app",
        "test_harness",
        "frontend_server",
        "scheduler",
        "monitor",
        "bot",
        "other",
    }
)
DEVICES = frozenset({"mac", "windows", "linux", "iphone", "ipad", "android", "other"})
BROWSERS = frozenset({"safari", "chrome", "firefox", "edge", "other"})

_BUILD = re.compile(r"^[0-9a-f]{7,40}$")
_VERSION = re.compile(r"^[0-9A-Za-z.+-]{1,32}$")
# The recording app's macOS build names itself and its build number:
# "Pablo/24 CFNetwork/3860.300.31 Darwin/25.2.0".
_RECORDING_APP_UA = re.compile(r"^Pablo/([0-9A-Za-z.+-]{1,32}) CFNetwork/")
_BOT_UA = re.compile(r"bot\b|crawler|spider|scan|Bot/|Search", re.IGNORECASE)

# Headers only the recording app sends: its enrolled install and the
# proof-of-possession that rides with it (see middleware/dpop.py). Both
# builds have sent them since enrolment shipped, so they identify the app
# even where it sends no User-Agent at all.
_RECORDING_APP_HEADERS = ("x-install-id", "dpop")


@dataclass(frozen=True)
class ClientInfo:
    app: str
    device: str
    browser: str
    version: str | None
    build: str | None
    source: str  # header | derived | mixed

    def log_fields(self) -> dict[str, str]:
        fields = {
            "client_app": self.app,
            "client_device": self.device,
            "client_browser": self.browser,
            "client_info_source": self.source,
        }
        if self.version is not None:
            fields["client_version"] = self.version
        if self.build is not None:
            fields["client_build"] = self.build
        return fields


def _enum_header(headers: Mapping[str, str], name: str, allowed: frozenset[str]) -> str | None:
    value = headers.get(name)
    if value is None:
        return None
    value = value.strip().lower()
    return value if value in allowed else None


def _pattern_header(headers: Mapping[str, str], name: str, pattern: re.Pattern[str]) -> str | None:
    value = headers.get(name)
    if value is None:
        return None
    value = value.strip()
    return value if pattern.match(value) else None


# First match wins, so the order matters: crawlers also start "Mozilla/".
_APP_RULES: tuple[tuple[Callable[[str], object], str], ...] = (
    (_RECORDING_APP_UA.match, "recording_app"),
    (lambda ua: ua.startswith("practice-harness"), "test_harness"),
    (lambda ua: ua.startswith(("Google-Cloud-Tasks", "Google-Cloud-Scheduler")), "scheduler"),
    (lambda ua: ua.startswith("GoogleStackdriverMonitoring"), "monitor"),
    (lambda ua: ua == "node" or ua.startswith(("node-fetch", "undici")), "frontend_server"),
    (_BOT_UA.search, "bot"),
    (lambda ua: ua.startswith("Mozilla/"), "browser"),
)

# First match wins. iPadOS Safari reports a Macintosh UA by default, so an
# iPad on Safari is logged as mac; nothing here tries to sniff touch.
_DEVICE_MARKERS: tuple[tuple[str, str], ...] = (
    ("iPhone", "iphone"),
    ("iPad", "ipad"),
    ("Android", "android"),
    ("Windows NT", "windows"),
    ("Macintosh", "mac"),
    ("Darwin/", "mac"),
    ("Linux", "linux"),
    ("X11", "linux"),
)


def derive_app(user_agent: str, headers: Mapping[str, str]) -> str:
    if any(headers.get(name) for name in _RECORDING_APP_HEADERS):
        return "recording_app"
    return next((app for matches, app in _APP_RULES if matches(user_agent)), "other")


def derive_device(user_agent: str, app: str) -> str:
    device = next((dev for marker, dev in _DEVICE_MARKERS if marker in user_agent), None)
    if device is not None:
        return device
    # The recording app ships for macOS and Windows. The macOS build always
    # names Darwin; the Windows build's HTTP client sends no User-Agent.
    return "windows" if app == "recording_app" else "other"


def derive_browser(user_agent: str, app: str) -> str:
    if app != "browser":
        return "other"
    if "Edg/" in user_agent:
        return "edge"
    if "Firefox/" in user_agent or "FxiOS/" in user_agent:
        return "firefox"
    if "Chrome/" in user_agent or "CriOS/" in user_agent:
        return "chrome"
    if "Safari/" in user_agent and "Version/" in user_agent:
        return "safari"
    return "other"


def derive_version(user_agent: str) -> str | None:
    match = _RECORDING_APP_UA.match(user_agent)
    return match.group(1) if match else None


def classify_client(headers: Mapping[str, str]) -> ClientInfo:
    """Classify a request's client. ``headers`` must be case-insensitive (Starlette's are)."""
    user_agent = (headers.get("user-agent") or "").strip()

    provided_app = _enum_header(headers, "x-client-app", APPS)
    provided_device = _enum_header(headers, "x-client-device", DEVICES)
    provided_browser = _enum_header(headers, "x-client-browser", BROWSERS)
    provided_version = _pattern_header(headers, "x-client-version", _VERSION)
    build = _pattern_header(headers, "x-client-build", _BUILD)

    app = provided_app or derive_app(user_agent, headers)
    device = provided_device or derive_device(user_agent, app)
    browser = provided_browser or derive_browser(user_agent, app)
    version = provided_version or derive_version(user_agent)

    provided = [provided_app, provided_device, provided_browser]
    if all(value is not None for value in provided):
        source = "header"
    elif any(value is not None for value in provided):
        source = "mixed"
    else:
        source = "derived"
    return ClientInfo(
        app=app, device=device, browser=browser, version=version, build=build, source=source
    )
