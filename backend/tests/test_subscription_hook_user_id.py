# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The optional subscription hooks are handed the signed-in account's id.

``app.routes.subscription`` is an optional extension point: each hook gets
the account's address and settings positionally and the account id as the
``user_id`` keyword, so an implementation can identify the account without
looking it up again by address. One test per call site, each asserting both
that the positional arguments are unchanged and that the keyword carries the
id of the account the request is for.
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from types import ModuleType
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from app.auth import service as auth_service
from app.models import Patient, User
from app.routes import internal_transcription as it
from app.routes import public_booking as public_booking_module
from app.routes import sessions as sessions_route
from app.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient

_SUBSCRIPTION_MODULE = f"{auth_service.__package__.rsplit('.', 1)[0]}.routes.subscription"


def _subscription_module(**hooks: object) -> ModuleType:
    module = ModuleType(_SUBSCRIPTION_MODULE)
    for name, hook in hooks.items():
        setattr(module, name, hook)
    return module


# --- require_active_subscription -> _fetch_subscription -----------------------


def test_subscription_fetch_is_given_the_signed_in_account_id(
    mock_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings().model_copy(update={"pablo_edition": "solo"})
    monkeypatch.setattr(auth_service, "get_settings", lambda: settings)
    fetch = MagicMock(return_value={"access_level": "full"})
    module = _subscription_module(_fetch_subscription=fetch)
    monkeypatch.setitem(sys.modules, _SUBSCRIPTION_MODULE, module)

    result = auth_service.require_active_subscription(MagicMock(), mock_user)

    assert result is mock_user
    fetch.assert_called_once_with(mock_user.email, settings, user_id=mock_user.id)


# --- public booking owner gate -> _fetch_subscription -------------------------


def test_booking_owner_gate_is_given_the_owners_account_id(
    mock_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = MagicMock(is_saas=True)
    monkeypatch.setattr(public_booking_module, "get_settings", lambda: settings)
    fetch = MagicMock(return_value={"access_level": "full"})
    module = _subscription_module(_fetch_subscription=fetch)
    monkeypatch.setitem(sys.modules, _SUBSCRIPTION_MODULE, module)

    public_booking_module._require_owner_may_accept_bookings(mock_user)

    fetch.assert_called_once_with(mock_user.email, settings, user_id=mock_user.id)


# --- recording gate -> check_recording_allowed --------------------------------


def test_recording_gate_is_given_the_signed_in_account_id(mock_user: User) -> None:
    hook = MagicMock(return_value=None)
    with patch.object(sessions_route, "check_recording_allowed", hook):
        sessions_route._gate_recording(mock_user)

    hook.assert_called_once()
    args, kwargs = hook.call_args
    assert args[0] == mock_user.email
    assert kwargs == {"user_id": mock_user.id}


def test_recording_hook_is_reached_by_an_audio_route_with_the_callers_id(
    client: TestClient, mock_user: User, mock_user_id: str
) -> None:
    """End to end through a real route: the id is the signed-in account's."""
    hook = MagicMock(return_value=None)
    with patch.object(sessions_route, "check_recording_allowed", hook):
        # The session does not exist; the gate runs before it is looked up.
        client.post("/api/sessions/no-such-session/upload-audio/init")

    hook.assert_called_once()
    assert hook.call_args.args[0] == mock_user.email
    assert hook.call_args.kwargs == {"user_id": mock_user_id}


# --- trial gate -> check_and_count_trial_session ------------------------------


def test_trial_gate_is_given_the_signed_in_account_id(mock_user: User) -> None:
    hook = MagicMock(return_value=None)
    with patch.object(sessions_route, "check_and_count_trial_session", hook):
        sessions_route._gate_trial_session(mock_user)

    hook.assert_called_once()
    args, kwargs = hook.call_args
    assert args[0] == mock_user.email
    assert kwargs == {"user_id": mock_user.id}


def test_trial_hook_is_reached_by_session_start_with_the_callers_id(
    client: TestClient, mock_repo: object, mock_user: User, mock_user_id: str
) -> None:
    now = datetime.now(UTC)
    patient = mock_repo.create(  # type: ignore[attr-defined]  # in-memory repo, typed as object by the fixture signature
        Patient(
            id=str(uuid.uuid4()),
            first_name="Jane",
            last_name="Smith",
            created_at=now,
            updated_at=now,
        ),
        mock_user_id,
    )
    hook = MagicMock(return_value=None)
    with patch.object(sessions_route, "check_and_count_trial_session", hook):
        client.post(
            "/api/sessions/schedule",
            json={
                "patient_id": patient.id,
                "scheduled_at": "2026-10-05T14:00:00Z",
                "source": "companion",
            },
        )

    hook.assert_called_once()
    assert hook.call_args.args[0] == mock_user.email
    assert hook.call_args.kwargs == {"user_id": mock_user_id}


# --- transcription completion -> record_recorded_session ----------------------


@pytest.fixture
def stored_account(mock_user: User) -> Iterator[MagicMock]:
    """The platform users row ``_meter_recorded_session`` reads the address from."""
    row = MagicMock(email=mock_user.email)
    db = MagicMock()
    db.get.return_value = row
    db.__enter__.return_value = db
    with patch.object(it, "create_standalone_session", return_value=db):
        yield db


def test_metering_is_given_the_account_id_and_still_the_stored_address(
    mock_user: User, stored_account: MagicMock
) -> None:
    hook = MagicMock(return_value=None)
    settings = MagicMock()
    with (
        patch.object(it, "record_recorded_session", hook),
        patch.object(it, "get_settings", return_value=settings),
    ):
        it._meter_recorded_session(mock_user.id, "session-1")

    # The address is still read from the account's row, keyed by the id.
    assert stored_account.get.call_args.args[1] == mock_user.id
    hook.assert_called_once_with(mock_user.email, "session-1", settings, user_id=mock_user.id)
