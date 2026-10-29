# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

from __future__ import annotations

from unittest.mock import patch

import pytest
from evals.anthropic_api import (
    _drop_null_properties,
    _strict_output_schema,
    direct_anthropic_gateway,
    direct_anthropic_model,
)


def test_direct_anthropic_model_removes_only_the_eval_prefix() -> None:
    assert direct_anthropic_model("anthropic-api:claude-sonnet-4-6") == "claude-sonnet-4-6"
    auto_model = direct_anthropic_model("anthropic-api-auto:claude-sonnet-4-6")
    assert auto_model == "auto:claude-sonnet-4-6"
    strict_model = direct_anthropic_model("anthropic-api-strict:claude-sonnet-5-5")
    assert strict_model == "strict:claude-sonnet-5-5"
    assert direct_anthropic_model("claude-sonnet-4-6") == "claude-sonnet-4-6"


def test_direct_anthropic_gateway_requires_its_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        direct_anthropic_gateway()


def test_direct_anthropic_gateway_injects_a_direct_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "eval-key")
    with patch("evals.anthropic_api.Anthropic") as client:
        gateway = direct_anthropic_gateway()
    client.assert_called_once_with(api_key="eval-key", timeout=180.0)
    assert gateway._client is client.return_value


def test_strict_output_schema_closes_objects_and_makes_optional_fields_nullable() -> None:
    schema = {
        "type": "object",
        "properties": {
            "required": {"type": "string"},
            "optional": {
                "type": "object",
                "properties": {"nested": {"type": "integer"}},
            },
        },
        "required": ["required"],
    }

    strict = _strict_output_schema(schema)

    assert strict["required"] == ["required", "optional"]
    assert strict["additionalProperties"] is False
    nested_object = strict["properties"]["optional"]
    assert nested_object["required"] == ["nested"]
    assert nested_object["additionalProperties"] is False
    assert nested_object["properties"]["nested"]["anyOf"][1] == {"type": "null"}
    assert schema["properties"]["optional"]["type"] == "object"


def test_strict_output_schema_uses_empty_members_inside_repeated_objects() -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"label": {"type": "string"}, "code": {"type": "string"}},
                    "required": ["label"],
                },
            }
        },
    }

    strict = _strict_output_schema(schema)

    item = strict["properties"]["items"]["anyOf"][0]["items"]
    assert item["required"] == ["label", "code"]
    assert item["properties"]["code"] == {"type": "string"}


def test_strict_output_schema_uses_empty_string_but_nullable_number() -> None:
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "count": {"type": "integer"}},
    }

    strict = _strict_output_schema(schema)

    assert strict["properties"]["text"] == {"type": "string"}
    assert strict["properties"]["count"]["anyOf"][1] == {"type": "null"}


def test_drop_null_properties_restores_omitted_optional_fields() -> None:
    assert _drop_null_properties({"kept": [1, {"gone": None}], "gone": None}) == {"kept": [1, {}]}
