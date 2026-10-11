# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A fake structured gateway that answers every call with something in it.

A test that only reads the prompts a draft sent still needs the draft to
come back with content: a draft with every field empty fails. This answers
each call with every plain string field of the schema it was sent filled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.services.structured_llm_gateway import FakeStructuredLLMGateway, StructuredCompletion

if TYPE_CHECKING:
    from app.reliability import RetryPolicy

ANSWER = "Noted."


def filled(schema: dict[str, Any]) -> dict[str, Any]:
    """An object for ``schema`` with each plain string field set to :data:`ANSWER`."""
    out: dict[str, Any] = {}
    for key, sub in (schema.get("properties") or {}).items():
        if not isinstance(sub, dict):
            continue
        if sub.get("type") == "object":
            out[key] = filled(sub)
        elif sub.get("type") == "string" and "enum" not in sub:
            out[key] = ANSWER
    return out


@dataclass
class AnsweringGateway(FakeStructuredLLMGateway):
    """Records every call like its parent and answers it from its schema."""

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
        self.default_response = StructuredCompletion(data=filled(response_schema))
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
