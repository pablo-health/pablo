# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The clinician's chart chat and the client-facing assistant are separate switches."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.main import chat_routers, portal_module_routers
from app.routes import chat, patient_chat, patient_messages
from app.settings import Settings

if TYPE_CHECKING:
    import pytest


def test_the_client_assistant_off_keeps_the_clinicians_chart_chat() -> None:
    routers = chat_routers(clinician_chat=True, portal_chat=False)
    assert chat.router in routers
    assert patient_chat.router not in routers


def test_the_client_assistant_can_be_on_without_the_clinician_chat() -> None:
    routers = chat_routers(clinician_chat=False, portal_chat=True)
    assert routers == [patient_chat.router]


def test_both_off_serves_no_chat_and_both_on_serves_both() -> None:
    assert chat_routers(clinician_chat=False, portal_chat=False) == []
    assert chat_routers(clinician_chat=True, portal_chat=True) == [
        chat.router,
        patient_chat.router,
    ]


def test_portal_messaging_does_not_depend_on_either_chat_switch() -> None:
    """Messaging is a portal module; the chat switches are not part of it."""
    routers = portal_module_routers(["messaging"])
    assert patient_messages.patient_messages_router in routers
    assert patient_chat.router not in routers


def test_the_client_assistant_is_off_unless_turned_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_PATIENT_PORTAL_CHAT", raising=False)
    assert Settings().enable_patient_portal_chat is False
