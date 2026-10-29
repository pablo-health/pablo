# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The prose-plan read keeps only tasks it can quote from the note.

What this catches: a model that invents a task ("Try a gratitude journal")
or paraphrases its quote reaching the clinician as if the note said it; a
task attributed to the wrong field; duplicates; a runaway list. And that the
call is given the plan fields and nothing else.
"""

from __future__ import annotations

from app.homework.drafting import (
    MAX_PROPOSALS,
    SYSTEM_PROMPT,
    HomeworkDrafter,
    PlanField,
    grounded,
)
from app.services.structured_llm_gateway import (
    FakeStructuredLLMGateway,
    StructuredCompletion,
)

_FIELDS = [
    PlanField(
        "psychotherapy.goal_plan",
        "Goal and plan",
        "Goal: fewer panic attacks. Continue CBT. Client agreed to log panic episodes "
        "in the app and practise diaphragmatic breathing twice a day.",
    ),
    PlanField(
        "plan.lifestyle_counseling",
        "Lifestyle counseling",
        "Discussed sleep hygiene; client will keep a sleep diary for two weeks.",
    ),
]


def _task(text: str, quote: str) -> dict[str, str]:
    return {"text": text, "quote": quote}


def test_an_unquoted_or_invented_task_is_dropped() -> None:
    raw = {
        "assignments": [
            _task("Log panic episodes in the app", "log panic episodes in the app"),
            _task("Try a gratitude journal", "keep a gratitude journal"),
            _task("Exercise more", ""),
        ]
    }
    kept = grounded(raw, _FIELDS)
    assert [t.text for t in kept] == ["Log panic episodes in the app"]


def test_quote_matching_ignores_case_whitespace_and_curly_quotes_only() -> None:
    raw = {
        "assignments": [
            _task("Keep a sleep diary", "Client  will keep a SLEEP diary"),
            _task("Practise breathing", "practise diaphragmatic breathing three times a day"),
        ]
    }
    kept = grounded(raw, _FIELDS)
    assert [(t.text, t.source_field) for t in kept] == [
        ("Keep a sleep diary", "plan.lifestyle_counseling")
    ]


def test_duplicates_and_overlong_lists_are_cut() -> None:
    quote = "log panic episodes"
    raw = {"assignments": [_task("Log panic episodes", quote)] * 3}
    assert len(grounded(raw, _FIELDS)) == 1
    many = {"assignments": [_task(f"Log panic episodes, step {n}", quote) for n in range(10)]}
    assert len(grounded(many, _FIELDS)) == MAX_PROPOSALS


def test_malformed_answers_propose_nothing() -> None:
    assert grounded({"assignments": "none"}, _FIELDS) == []
    assert grounded({}, _FIELDS) == []
    assert grounded({"assignments": [None, 3, {"text": 5}]}, _FIELDS) == []


def test_the_call_sees_only_the_plan_fields_and_is_told_not_to_invent() -> None:
    gateway = FakeStructuredLLMGateway(
        default_response=StructuredCompletion(
            data={"assignments": [_task("Keep a sleep diary", "keep a sleep diary")]}
        )
    )
    tasks = HomeworkDrafter(llm_gateway=gateway, model="test-model").draft(_FIELDS)
    assert [t.text for t in tasks] == ["Keep a sleep diary"]
    (call,) = gateway.calls
    assert call["user_prompt"].startswith("Goal and plan:\nGoal: fewer panic attacks.")
    assert "Lifestyle counseling:\n" in call["user_prompt"]
    assert call["temperature"] == 0.0
    assert "Never invent a task" in SYSTEM_PROMPT
    assert "medication instructions" in SYSTEM_PROMPT


def test_no_fields_means_no_call() -> None:
    gateway = FakeStructuredLLMGateway()
    assert HomeworkDrafter(llm_gateway=gateway, model="m").draft([]) == []
    assert gateway.calls == []
