# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Direct Anthropic gateway for synthetic evals only.

Production model routing deliberately uses managed cloud endpoints. This
adapter lives under ``backend/evals`` and is selected only by an eval CLI's
explicit ``anthropic-api:`` model prefix.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from anthropic import Anthropic
from app.reliability import LLM_REQUEST, Idempotency, call_with_retry
from app.services.structured_llm_gateway import (
    AnthropicStructuredLLMGateway,
    StructuredCompletion,
    StructuredLLMGateway,
    StructuredOutputTruncatedError,
)

if TYPE_CHECKING:
    from app.reliability import RetryPolicy


class DirectAnthropicGateway(AnthropicStructuredLLMGateway):
    """Direct API gateway, including models that require native JSON output."""

    def complete_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        max_output_tokens: int,
        temperature: float = 0.3,
        thinking_budget: int | None = None,
        timeout_seconds: float | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> StructuredCompletion:
        strict_output = model.startswith("strict:")
        model = model.removeprefix("strict:")
        auto_tool = model.startswith("auto:")
        model = model.removeprefix("auto:")
        if strict_output:
            del temperature, thinking_budget
            wire_schema = _strict_output_schema(response_schema)
            response = call_with_retry(
                lambda: self._get_client().messages.create(
                    model=model,
                    max_tokens=max_output_tokens,
                    system=[
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user_prompt}],
                    output_config={
                        "format": {
                            "type": "json_schema",
                            "schema": wire_schema,
                        }
                    },
                    **({"timeout": timeout_seconds} if timeout_seconds is not None else {}),
                ),
                policy=retry_policy or LLM_REQUEST,
                idempotency=Idempotency.SAFE,
            )
            if response.stop_reason == "max_tokens":
                raise StructuredOutputTruncatedError(
                    f"LLM output truncated at max_output_tokens={max_output_tokens} (model={model})"
                )
            text = next(
                (
                    block.text
                    for block in response.content
                    if getattr(block, "type", None) == "text"
                ),
                None,
            )
            if text is None:
                raise ValueError("Anthropic structured response had no text block")
            data = _drop_null_properties(json.loads(text))
            if not isinstance(data, dict):
                raise ValueError(f"Structured output was not an object ({type(data).__name__})")
            return StructuredCompletion(
                data=data,
                output_tokens=getattr(response.usage, "output_tokens", None),
                finish_reason="safety" if response.stop_reason == "refusal" else "stop",
            )
        if not auto_tool and not model.startswith("claude-sonnet-5-5"):
            return super().complete_structured(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_schema=response_schema,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                thinking_budget=thinking_budget,
                timeout_seconds=timeout_seconds,
                retry_policy=retry_policy,
            )

        # Sonnet 5.5 rejects forced tool choice and sampling controls.
        del temperature, thinking_budget
        tool = {
            "name": self._TOOL_NAME,
            "description": "Always call this tool to return the requested structured result.",
            "input_schema": response_schema,
        }
        response = call_with_retry(
            lambda: self._get_client().messages.create(
                model=model,
                max_tokens=max_output_tokens,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=[tool],
                tool_choice={"type": "auto"},
                messages=[
                    {
                        "role": "user",
                        "content": f"{user_prompt}\n\nCall {self._TOOL_NAME} with the result.",
                    }
                ],
                **({"timeout": timeout_seconds} if timeout_seconds is not None else {}),
            ),
            policy=retry_policy or LLM_REQUEST,
            idempotency=Idempotency.SAFE,
        )
        if response.stop_reason == "max_tokens":
            raise StructuredOutputTruncatedError(
                f"LLM output truncated at max_output_tokens={max_output_tokens} (model={model})"
            )
        data = next(
            (
                block.input
                for block in response.content
                if getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == self._TOOL_NAME
            ),
            None,
        )
        if data is None:
            raise ValueError(f"Anthropic response had no '{self._TOOL_NAME}' tool call")
        if not isinstance(data, dict):
            raise ValueError(f"Structured tool input was not an object ({type(data).__name__})")
        return StructuredCompletion(
            data=data,
            output_tokens=getattr(response.usage, "output_tokens", None),
            finish_reason="safety" if response.stop_reason == "refusal" else "stop",
        )


def direct_anthropic_gateway() -> StructuredLLMGateway:
    """Build the eval-only direct gateway from ``ANTHROPIC_API_KEY``."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("anthropic-api: evals need ANTHROPIC_API_KEY set")

    return DirectAnthropicGateway(client=Anthropic(api_key=api_key, timeout=180.0))


def direct_anthropic_model(model: str) -> str:
    """Remove the eval-only prefix before the shared gateway calls Claude."""
    if model.startswith("anthropic-api-auto:"):
        return f"auto:{model.removeprefix('anthropic-api-auto:')}"
    if model.startswith("anthropic-api-strict:"):
        return f"strict:{model.removeprefix('anthropic-api-strict:')}"
    return model.removeprefix("anthropic-api:")


def _strict_output_schema(response_schema: dict[str, Any]) -> dict[str, Any]:
    """Close objects and express omitted properties as required nullable values."""
    schema = deepcopy(response_schema)

    def close(node: Any, *, array_item: bool = False) -> None:
        if not isinstance(node, dict):
            return
        properties = node.get("properties")
        if node.get("type") == "object" and isinstance(properties, dict):
            originally_required = set(node.get("required", []))
            for name, child in properties.items():
                close(child, array_item=array_item)
                # Section containers can always be present, and text already has
                # an explicit empty representation in Pablo's draft contract.
                # Preserve null only for kinds without one (arrays and numbers).
                # Repeated-object members likewise use their kind's empty value.
                # This keeps a large note below Claude's union compilation cap.
                if (
                    name not in originally_required
                    and not array_item
                    and child.get("type") not in {"object", "string"}
                ):
                    properties[name] = {"anyOf": [child, {"type": "null"}]}
            node["required"] = list(properties)
            node["additionalProperties"] = False
        items = node.get("items")
        if items is not None:
            close(items, array_item=True)
        for keyword in ("anyOf", "oneOf", "allOf"):
            for child in node.get(keyword, []):
                close(child, array_item=array_item)

    close(schema)
    return schema


def _drop_null_properties(value: Any) -> Any:
    """Restore the gateway contract in which optional fields may be absent."""
    if isinstance(value, dict):
        return {key: _drop_null_properties(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_null_properties(item) for item in value]
    return value
