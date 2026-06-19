"""Unit tests for best-effort JSON-object extraction from model text."""

from __future__ import annotations

import pytest

from backend.app.services.llm_json import extract_json_object


def test_bare_object() -> None:
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_fenced_json_block() -> None:
    raw = 'Here:\n```json\n{"a": 1, "b": "x"}\n```\nDone.'
    assert extract_json_object(raw) == {"a": 1, "b": "x"}


def test_object_embedded_in_prose() -> None:
    raw = 'The verdict is {"a": 1} as shown.'
    assert extract_json_object(raw) == {"a": 1}


def test_braces_inside_strings_do_not_unbalance() -> None:
    raw = 'prefix {"a": "has } brace", "b": 2} suffix'
    assert extract_json_object(raw) == {"a": "has } brace", "b": 2}


@pytest.mark.parametrize("raw", [None, "", "not json at all", "[1, 2, 3]", "{broken"])
def test_unparseable_returns_none(raw: str | None) -> None:
    assert extract_json_object(raw) is None
