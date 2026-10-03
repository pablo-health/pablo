# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A structured gateway answered by an HTTP service rather than a model.

The end-to-end stack has no model credentials and should not need any: a
spec that types a sentence wants the same reading every run, not whatever a
model says that day. This posts the call to a stand-in (``scripts/fake_llm.py``)
that answers each known prompt with a fixed reply. What comes back is
handed to the caller exactly as a model's reply would be, so everything
after the call — validation, refusals, the confirmation echo — runs as it
does in production.

Only reachable through a setting that refuses to load outside
``ENVIRONMENT=development`` (see ``Settings.availability_parse_base_url``).
"""

from __future__ import annotations

from typing import Any

import httpx

from .structured_llm_gateway import StructuredCompletion, StructuredLLMGateway

_TIMEOUT_SECONDS = 10.0


class HttpStructuredLLMGateway(StructuredLLMGateway):
    """POST each call to ``{base_url}/v1/structured`` and return its ``data``."""

    def __init__(self, base_url: str) -> None:
        self._url = base_url.rstrip("/") + "/v1/structured"

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
    ) -> StructuredCompletion:
        response = httpx.post(
            self._url,
            json={
                "model": model,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response_schema": response_schema,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
                "thinking_budget": thinking_budget,
            },
            timeout=timeout_seconds if timeout_seconds is not None else _TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        data = body.get("data")
        if not isinstance(data, dict):
            raise ValueError("structured stand-in returned no data object")
        return StructuredCompletion(
            data=data,
            output_tokens=body.get("output_tokens"),
            finish_reason=str(body.get("finish_reason", "stop")),
        )
