# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Unit tests for the faithfulness judge's prompt assembly + verdict parsing.

The live scoring path (real Vertex judge) is exercised ad-hoc by
``run_note_generation.py``; these tests pin the pure logic — that
``judge_directives`` reach the prompt and that a structured verdict is parsed —
using an injected fake gateway so they need no network or credentials.
"""

from __future__ import annotations

from typing import Any

from backend.app.services.structured_llm_gateway import StructuredCompletion
from backend.evals.scorers.llm_judge_faithfulness import score


class _CapturingGateway:
    """Fake structured gateway that records the last call and replays a verdict."""

    def __init__(self, verdict: dict[str, Any]) -> None:
        self._verdict = verdict
        self.last_user_prompt: str | None = None
        self.last_schema: dict[str, Any] | None = None

    def complete_structured(
        self,
        *,
        model: str,  # noqa: ARG002
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,
        response_schema: dict[str, Any],
        max_output_tokens: int,  # noqa: ARG002
        temperature: float = 0.3,  # noqa: ARG002
        thinking_budget: int | None = None,  # noqa: ARG002
    ) -> StructuredCompletion:
        self.last_user_prompt = user_prompt
        self.last_schema = response_schema
        return StructuredCompletion(data=self._verdict)


def test_directives_are_injected_into_the_prompt() -> None:
    gw = _CapturingGateway(
        {"passes": True, "hallucinated_facts": [], "missing_facts": [], "judge_notes": "ok"}
    )
    directives = ["Do not escalate passive SI to active SI.", "Do not invent a medication."]

    score(
        transcript="[00:00] Client: I feel low.",
        generated_soap='{"assessment": {"impression": "low mood"}}',
        directives=directives,
        gateway=gw,
    )

    assert gw.last_user_prompt is not None
    assert "AUDIT DIRECTIVES" in gw.last_user_prompt
    for d in directives:
        assert d in gw.last_user_prompt


def test_no_directives_omits_the_block() -> None:
    gw = _CapturingGateway(
        {"passes": True, "hallucinated_facts": [], "missing_facts": [], "judge_notes": "ok"}
    )

    score(transcript="t", generated_soap="{}", gateway=gw)

    assert gw.last_user_prompt is not None
    assert "AUDIT DIRECTIVES" not in gw.last_user_prompt


def test_schema_requires_the_four_verdict_keys() -> None:
    gw = _CapturingGateway(
        {"passes": True, "hallucinated_facts": [], "missing_facts": [], "judge_notes": "ok"}
    )

    score(transcript="t", generated_soap="{}", gateway=gw)

    assert gw.last_schema is not None
    assert set(gw.last_schema.get("required", [])) == {
        "hallucinated_facts",
        "missing_facts",
        "passes",
        "judge_notes",
    }


def test_verdict_is_parsed_from_gateway_data() -> None:
    verdict = {
        "passes": False,
        "hallucinated_facts": [
            {
                "claim": "Confirmed Bipolar I",
                "where": "assessment",
                "why_unsupported": "rule-out only",
            }
        ],
        "missing_facts": [{"fact": "family history", "criticality": "high", "why_critical": "dx"}],
        "judge_notes": "invented a confirmed diagnosis",
    }
    gw = _CapturingGateway(verdict)

    result = score(transcript="t", generated_soap="{}", gateway=gw)

    assert result.passes is False
    assert len(result.hallucinated_facts) == 1
    assert result.hallucinated_facts[0]["where"] == "assessment"
    assert result.missing_facts[0]["criticality"] == "high"
    assert "confirmed diagnosis" in result.judge_notes
