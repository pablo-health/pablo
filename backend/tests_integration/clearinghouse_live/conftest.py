# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The live vendor lane: the clearinghouse adapter against the vendor's test mode.

The recorded fixtures under ``tests/fixtures/clearinghouse`` prove our code;
they cannot prove the vendor still answers that way. This lane runs the same
operations for real, with your own clearinghouse test key, and shape-diffs
every answer against its recording so drift fails loudly instead of waiting
for a real claim.

Opt-in and test-mode only:

* Without ``CLEARINGHOUSE_LIVE_API_KEY`` every test here skips with a reason.
* With a key that the credential provider does not classify as ``test`` the
  whole run aborts at collection, before any call is made. Nothing in this
  directory ever talks to a production payer.

The key is read from the environment once and handed to the adapter; it is
never written to a file, a log line, or an assertion message. Nor are the
vendor's mock member ids or the claim's diagnosis codes — a failure prints
key paths and JSON types, not values.

No ``app.*`` imports at module level: pytest loads nested conftests before the
parent conftest's ``pytest_configure`` runs, and app settings freeze at first
import (see ``tests_integration/database/conftest.py``).
"""

from __future__ import annotations

import copy
import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.claims.credentials import ClearinghouseCredentials
    from app.claims.stedi import StediClearinghouseClient
    from app.models.claims_transport import ClaimSubmissionResult

KEY_ENV = "CLEARINGHOUSE_LIVE_API_KEY"

_HERE = Path(__file__).resolve().parent
FIXTURES = _HERE.parents[1] / "tests" / "fixtures" / "clearinghouse"

#: The vendor's test payer: every claim to it is acknowledged (277CA) and paid
#: in full (835) within minutes, and nothing reaches a real payer.
TEST_PAYER_ID = "STEDI"

#: A claim's patient control number is echoed back on the 277CA and the 835,
#: which is how the round trip finds its own transactions. Kept well under
#: the vendor's limit and prefixed so a stray one is recognisable in the
#: vendor's portal.
_CONTROL_NUMBER_PREFIX = "LIVE"
_CONTROL_NUMBER_RANDOM_BYTES = 6
MAX_CONTROL_NUMBER_LENGTH = 17

_REQUEST_TIMEOUT_SECONDS = 30.0


def _live_key() -> str | None:
    return os.environ.get(KEY_ENV) or None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip this lane without a key; refuse to run it with a production key."""
    ours = [item for item in items if _HERE in item.path.parents]
    if not ours:
        return
    key = _live_key()
    if key is None:
        reason = (
            f"{KEY_ENV} is not set: export your clearinghouse test-mode API key "
            "to run the live vendor lane"
        )
        for item in ours:
            item.add_marker(pytest.mark.skip(reason=reason))
        return

    from app.claims.credentials import mode_for_key  # noqa: PLC0415

    if mode_for_key(key) != "test":
        pytest.exit(
            f"{KEY_ENV} is not a test-mode key; the live vendor lane never runs "
            "against the production environment",
            returncode=3,
        )


class ResponseRecorder:
    """Keeps the most recent raw response the adapter received.

    The adapter returns parsed models with unknown fields dropped, but the
    shape-diff needs the vendor's full JSON — an ``httpx`` response hook is
    the one place both the adapter's calls and the lane's own raw calls pass
    through.
    """

    def __init__(self) -> None:
        self.last: httpx.Response | None = None

    def __call__(self, response: httpx.Response) -> None:
        response.read()
        self.last = response

    def last_json(self) -> dict[str, Any]:
        assert self.last is not None, "no response recorded yet"
        body = self.last.json()
        assert isinstance(body, dict)
        return body

    def last_status(self) -> int:
        assert self.last is not None, "no response recorded yet"
        return self.last.status_code


@dataclass
class LiveClient:
    """One clearinghouse test account: the adapter, plus raw access for the
    endpoints the adapter does not wrap (polling, reports)."""

    # Nothing here belongs in a failure printout: the credentials carry the
    # key (already repr-hidden, but belt and braces) and the rest is noise.
    credentials: ClearinghouseCredentials = field(repr=False)
    adapter: StediClearinghouseClient = field(repr=False)
    http: httpx.Client = field(repr=False)
    recorder: ResponseRecorder = field(repr=False)

    def get_raw(self, url: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        return self.http.get(
            url, params=params, headers={"Authorization": f"Key {self.credentials.api_key}"}
        )

    def post_raw(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
        return self.http.post(
            url, json=json, headers={"Authorization": f"Key {self.credentials.api_key}"}
        )


def _build_live_client(
    credentials: ClearinghouseCredentials, recorder: ResponseRecorder
) -> LiveClient:
    from app.claims.stedi import StediClearinghouseClient  # noqa: PLC0415

    http = httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS, event_hooks={"response": [recorder]})
    return LiveClient(
        credentials=credentials,
        adapter=StediClearinghouseClient(credentials, client=http),
        http=http,
        recorder=recorder,
    )


@pytest.fixture(scope="session")
def live() -> Iterator[LiveClient]:
    from app.claims.credentials import ClearinghouseCredentials, mode_for_key  # noqa: PLC0415

    key = _live_key()
    assert key is not None
    assert mode_for_key(key) == "test"
    client = _build_live_client(
        ClearinghouseCredentials(api_key=key, mode="test"), ResponseRecorder()
    )
    yield client
    client.http.close()


def fixture_shape(name: str) -> dict[str, Any]:
    """The recorded JSON body for ``name`` under ``tests/fixtures/clearinghouse``."""
    body = json.loads((FIXTURES / name).read_text())
    assert isinstance(body, dict)
    return body


_JSON_TYPES: tuple[tuple[type | tuple[type, ...], str], ...] = (
    (bool, "boolean"),  # before int: bool is an int subclass
    ((int, float), "number"),
    (str, "string"),
    (list, "array"),
    (dict, "object"),
)


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    return next(
        (name for kind, name in _JSON_TYPES if isinstance(value, kind)), type(value).__name__
    )


def _shape_diff(live_value: object, recorded: object, path: str, diffs: list[str]) -> None:
    live_type, recorded_type = _json_type(live_value), _json_type(recorded)
    if recorded_type == "null":
        # A recorded null says nothing about the field's real type.
        return
    if live_type != recorded_type:
        diffs.append(f"{path}: recorded {recorded_type}, live {live_type}")
        return
    if isinstance(recorded, dict) and isinstance(live_value, dict):
        for key, recorded_child in recorded.items():
            if key not in live_value:
                diffs.append(f"{path}.{key}: missing from live response")
                continue
            _shape_diff(live_value[key], recorded_child, f"{path}.{key}", diffs)
    elif isinstance(recorded, list) and isinstance(live_value, list):
        if not recorded:
            return
        if not live_value:
            diffs.append(f"{path}[]: recorded has elements, live is empty")
            return
        _shape_diff(live_value[0], recorded[0], f"{path}[0]", diffs)


def assert_same_shape(live_value: dict[str, Any], recorded: dict[str, Any]) -> None:
    """Every key in the recording is present in the live body with the same JSON type.

    Values may differ (ids, timestamps, amounts); a key the vendor added is
    fine; a key it dropped or retyped is drift and fails with the path list.
    Lists are compared on their first element. Only paths and type names
    ever reach the assertion message — never values — and this frame is
    hidden from the traceback so pytest does not print the bodies as its
    arguments either.
    """
    __tracebackhide__ = True
    diffs: list[str] = []
    _shape_diff(live_value, recorded, "$", diffs)
    assert not diffs, "response shape drifted from the recording:\n  " + "\n  ".join(diffs)


def fresh_control_number() -> str:
    number = _CONTROL_NUMBER_PREFIX + secrets.token_hex(_CONTROL_NUMBER_RANDOM_BYTES).upper()
    assert len(number) <= MAX_CONTROL_NUMBER_LENGTH
    return number


def fresh_idempotency_key() -> str:
    """A key no other submission in this run (or the last 24 h) has used.

    The vendor keys replay detection on it, so every submission that is
    meant to be a new claim — the reject tests included — needs its own;
    sharing one would answer later claims with the first one's cached
    result.
    """
    return secrets.token_urlsafe(24)


def submission_body(control_number: str) -> dict[str, Any]:
    """The recorded test-payer claim, re-keyed to ``control_number``.

    A fresh copy every call: the reject tests each mutate one field of it.
    """
    body = copy.deepcopy(fixture_shape("837p_request_test_payer.json"))
    body["claimInformation"]["patientControlNumber"] = control_number
    body["claimInformation"]["serviceLines"][0]["providerControlNumber"] = control_number + "L1"
    return body


@dataclass
class SubmittedClaim:
    """The one accepted claim this run submits; the success and round-trip
    tests both read it so the vendor sees a single new claim per run."""

    # The bodies stay out of the repr so a failing test prints control
    # numbers and timestamps, not the claim.
    body: dict[str, Any] = field(repr=False)
    idempotency_key: str = field(repr=False)
    result: ClaimSubmissionResult = field(repr=False)
    raw: dict[str, Any] = field(repr=False)
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def control_number(self) -> str:
        number = self.body["claimInformation"]["patientControlNumber"]
        assert isinstance(number, str)
        return number


@pytest.fixture(scope="session")
def submitted_claim(live: LiveClient) -> SubmittedClaim:
    from app.models.claims_transport import ClaimSubmissionRequest  # noqa: PLC0415

    body = submission_body(fresh_control_number())
    key = fresh_idempotency_key()
    submitted_at = datetime.now(UTC)
    result = live.adapter.submit_claim(
        ClaimSubmissionRequest.model_validate(body), idempotency_key=key
    )
    return SubmittedClaim(
        body=body,
        idempotency_key=key,
        result=result,
        raw=live.recorder.last_json(),
        submitted_at=submitted_at,
    )
