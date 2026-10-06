# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether the practice asks clients to agree to AI-assisted notes.

The routes, and the promise that the consent line on a note is never something
a model drafts: no note type's output schema or field list carries it.
"""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.db.platform_models import PracticeRow
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.services.note_generation_service import (
    _build_registry_response_schema,
    _fields_block,
)

_PATH = "/api/users/me/practice/ai-notes-consent"


def _practice(**overrides: Any) -> SimpleNamespace:
    values = {
        "id": "practice-1",
        "owner_email": "test@example.com",
        "ask_clients_about_ai_notes": True,
        "audio_retention_days": 365,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _call(client: Any, method: str, practice: Any, **kwargs: Any) -> Any:
    session = MagicMock()
    session.get.return_value = practice
    with (
        patch(
            "app.auth.service._resolve_practice_from_email",
            return_value=("practice-1", "practice_1"),
        ),
        patch("app.db.get_db_session", return_value=session),
    ):
        return getattr(client, method)(_PATH, **kwargs)


def test_the_model_defaults_to_asking() -> None:
    column = PracticeRow.__table__.c.ask_clients_about_ai_notes
    assert column.nullable is False
    assert column.default is not None
    assert column.default.arg is True
    assert column.server_default is not None
    assert str(column.server_default.arg) == "true"


class TestRoutes:
    def test_reads_the_setting_and_the_retention_window(self, client: Any) -> None:
        response = _call(client, "get", _practice(audio_retention_days=90))
        assert response.status_code == 200
        assert response.json() == {
            "ask_clients_about_ai_notes": True,
            "audio_retention_days": 90,
            "can_change": True,
        }

    def test_any_clinician_in_the_practice_can_read_it(self, client: Any) -> None:
        practice = _practice(owner_email="someone-else@example.com")
        response = _call(client, "get", practice)
        assert response.status_code == 200
        assert response.json()["ask_clients_about_ai_notes"] is True
        assert response.json()["can_change"] is False

    def test_owner_turns_it_off_and_on(self, client: Any) -> None:
        practice = _practice()
        off = _call(client, "put", practice, json={"ask_clients_about_ai_notes": False})
        assert off.status_code == 200
        assert off.json()["ask_clients_about_ai_notes"] is False
        assert practice.ask_clients_about_ai_notes is False
        assert _call(client, "get", practice).json()["ask_clients_about_ai_notes"] is False

        on = _call(client, "put", practice, json={"ask_clients_about_ai_notes": True})
        assert on.json()["ask_clients_about_ai_notes"] is True
        assert practice.ask_clients_about_ai_notes is True

    def test_non_owner_cannot_change_it(self, client: Any) -> None:
        practice = _practice(owner_email="someone-else@example.com")
        response = _call(client, "put", practice, json={"ask_clients_about_ai_notes": False})
        assert response.status_code == 403
        assert practice.ask_clients_about_ai_notes is True

    def test_no_practice_is_404(self, client: Any) -> None:
        with patch("app.auth.service._resolve_practice_from_email", return_value=None):
            response = client.get(_PATH)
        assert response.status_code == 404

    def test_rejects_a_missing_value(self, client: Any) -> None:
        assert _call(client, "put", _practice(), json={}).status_code == 422


#: What the consent line on a note says or is called, in any spelling a schema
#: might use. "Consent" alone is not here: an intake note legitimately records
#: whether a treatment consent form was signed.
_CONSENT_LINE_MARKERS = (
    "ai-assisted",
    "ai assisted",
    "ai_assisted",
    "ai consent",
    "ai_consent",
    "ai-consent",
    "ai notes",
    "ai_notes",
    "no consent on file",
)


def _registry() -> NoteTypeRegistry:
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    return registry


@pytest.mark.parametrize("key", _registry().keys())
def test_no_note_type_asks_the_model_for_the_consent_line(key: str) -> None:
    definition = _registry().get(key)
    asked_for = (
        json.dumps(_build_registry_response_schema(definition)) + _fields_block(definition)
    ).lower()
    for marker in _CONSENT_LINE_MARKERS:
        assert marker not in asked_for, (key, marker)
