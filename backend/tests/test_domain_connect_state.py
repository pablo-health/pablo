# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The ``state`` a Domain Connect link carries: only this deployment's, only
for the practice it was issued to, and only for 15 minutes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from app.services.domain_connect_state import (
    MAX_AGE_SECONDS,
    ConnectState,
    DomainConnectStateError,
    mint_state,
    verify_state,
)

KEY = b"dummy-state-key"
ISSUED = ConnectState(practice_id="practice-1", apex="example.com", service_id="practice-domain")
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _at(moment: datetime) -> Any:
    return patch("app.services.domain_connect_state.utc_now", return_value=moment)


def _minted() -> str:
    with _at(NOW):
        return mint_state(KEY, ISSUED)


def test_round_trips() -> None:
    value = _minted()
    with _at(NOW + timedelta(minutes=14)):
        assert verify_state(KEY, value, "practice-1") == ISSUED


def test_two_links_get_different_states() -> None:
    assert _minted() != _minted()


def test_expires_after_fifteen_minutes() -> None:
    value = _minted()
    with (
        _at(NOW + timedelta(seconds=MAX_AGE_SECONDS + 1)),
        pytest.raises(DomainConnectStateError, match="expired"),
    ):
        verify_state(KEY, value, "practice-1")


def test_another_practice_cannot_use_it() -> None:
    with _at(NOW), pytest.raises(DomainConnectStateError, match="another practice"):
        verify_state(KEY, _minted(), "practice-2")


def test_a_changed_payload_is_refused() -> None:
    body, _, signature = _minted().partition(".")
    tampered = ("A" if body[0] != "A" else "B") + body[1:]
    with _at(NOW), pytest.raises(DomainConnectStateError, match="signature"):
        verify_state(KEY, f"{tampered}.{signature}", "practice-1")


def test_another_key_is_refused() -> None:
    with _at(NOW), pytest.raises(DomainConnectStateError, match="signature"):
        verify_state(b"dummy-other-key", _minted(), "practice-1")


@pytest.mark.parametrize("value", ["", "no-dot", ".sig", "body."])
def test_malformed_is_refused(value: str) -> None:
    with pytest.raises(DomainConnectStateError):
        verify_state(KEY, value, "practice-1")
