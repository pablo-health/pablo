# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Goal-based EHR navigation service.

Uses per-EHR system prompts and Gemini 2.5 Flash-Lite on Vertex AI to guide
browser navigation step by step until the companion app reaches the SOAP form.

HIPAA: No PHI reaches the LLM — the companion app strips patient names
client-side before calling this endpoint.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from ..models.ehr_route import GoalNavigationRequest, GoalNavigationResponse

if TYPE_CHECKING:
    from ..repositories.ehr_prompt import EhrPromptRepository

logger = logging.getLogger(__name__)


class EhrNavigationService(ABC):
    """Abstract interface for goal-based EHR navigation."""

    @abstractmethod
    async def get_ehr_prompt(self, ehr_system: str) -> str:
        """Load the system prompt for an EHR system."""

    @abstractmethod
    def build_user_prompt(self, request: GoalNavigationRequest) -> str:
        """Construct the user prompt from structured request fields."""

    @abstractmethod
    async def navigate(self, request: GoalNavigationRequest) -> GoalNavigationResponse:
        """Call LLM to determine the next navigation action."""


class GeminiEhrNavigationService(EhrNavigationService):
    """Production implementation using Gemini 2.5 Flash-Lite via Vertex AI."""

    def __init__(self, model: str, prompt_repo: EhrPromptRepository) -> None:
        self.model = model
        self._prompt_repo = prompt_repo
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazily initialize the google.genai client."""
        if self._client is None:
            from google import genai

            self._client = genai.Client(vertexai=True)
        return self._client

    async def get_ehr_prompt(self, ehr_system: str) -> str:
        """Load the system prompt for an EHR system from the prompt repository."""
        prompt = self._prompt_repo.get(ehr_system)
        if prompt is None:
            msg = f"No navigation prompt configured for EHR system '{ehr_system}'"
            raise LookupError(msg)
        return prompt.system_prompt

    def build_user_prompt(self, request: GoalNavigationRequest) -> str:
        """Construct the user prompt from structured request fields."""
        actions_text = ""
        if request.previous_actions:
            actions_text = "ACTIONS TAKEN SO FAR:\n"
            for i, a in enumerate(request.previous_actions, 1):
                actions_text += f"  {i}. {a.action} → {a.target} → {a.result}\n"

        failed_text = ""
        if request.failed_action:
            failed_text = f"\nLAST ACTION FAILED: {request.failed_action}\n"

        return (
            f"GOAL: {request.goal}\n\n"
            f"CURRENT URL: {request.current_url}\n\n"
            f"{actions_text}{failed_text}"
            "CURRENT PAGE DOM (interactive elements only, "
            "patient names replaced with [PATIENT]):\n"
            f"{request.dom_snapshot}\n\n"
            "Return a single JSON object with your next action."
        )

    def _parse_response(self, text: str) -> GoalNavigationResponse:
        """Parse and validate the LLM JSON response."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1]).strip()

        data = json.loads(cleaned)

        action = data.get("action", "none")
        valid_actions = {"click", "navigate", "wait", "fill", "none"}
        if action not in valid_actions:
            action = "none"

        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))

        form_fields = None
        if data.get("form_fields") and isinstance(data["form_fields"], dict):
            form_fields = {str(k): str(v) for k, v in data["form_fields"].items()}

        return GoalNavigationResponse(
            action=action,
            selector=str(data.get("selector", "")),
            reasoning=str(data.get("reasoning", "")),
            confidence=confidence,
            is_on_target_page=bool(data.get("is_on_target_page", False)),
            form_fields=form_fields,
            alternative_plan=data.get("alternative_plan"),
        )

    async def navigate(self, request: GoalNavigationRequest) -> GoalNavigationResponse:
        """Call Gemini to determine the next navigation action."""
        try:
            from google.genai import types

            system_prompt = await self.get_ehr_prompt(request.ehr_system.value)
            user_prompt = self.build_user_prompt(request)

            client = self._get_client()
            response = client.models.generate_content(
                model=self.model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    temperature=0.1,
                    max_output_tokens=500,
                ),
            )
            return self._parse_response(response.text)
        except LookupError:
            raise
        except json.JSONDecodeError as err:
            logger.exception("Failed to parse LLM response as JSON")
            msg = f"LLM returned invalid JSON: {err}"
            raise ValueError(msg) from err
        except ImportError as err:
            msg = "google-genai package is required for GeminiEhrNavigationService"
            raise RuntimeError(msg) from err
        except Exception as err:
            logger.exception("EHR navigation LLM call failed")
            msg = f"EHR navigation LLM call failed: {err}"
            raise RuntimeError(msg) from err


class MockEhrNavigationService(EhrNavigationService):
    """Mock implementation for testing."""

    async def get_ehr_prompt(self, ehr_system: str) -> str:
        return f"Mock system prompt for {ehr_system}"

    def build_user_prompt(self, request: GoalNavigationRequest) -> str:
        return f"Mock user prompt for goal: {request.goal}"

    async def navigate(self, _request: GoalNavigationRequest) -> GoalNavigationResponse:
        return GoalNavigationResponse(
            action="click",
            selector="a[href='/events/123-260323']",
            reasoning="Mock: found direct link to event",
            confidence=0.95,
            is_on_target_page=False,
            form_fields=None,
            alternative_plan="Try calendar route",
        )
