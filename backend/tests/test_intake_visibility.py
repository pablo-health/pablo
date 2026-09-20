# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether a question is shown, given what the patient has answered.

Most of this file is one parametrised test over a table of cases stored as
JSON, because the table is the interesting part and because the browser
runs the same table through its own port of the evaluator
(``frontend/src/lib/intake/__tests__/visibility.test.ts``). Two
implementations of one rule drift; two implementations pinned to one set of
cases cannot drift quietly.

What is asserted beside the table is what the table cannot say: that every
operator the rule schema admits actually appears in it, so an operator
added to :mod:`app.intake.rules` and forgotten here fails rather than going
untested.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from app.intake.rules import ANSWER_OPS, INSTRUMENT_OPS, VisibleWhen
from app.intake.visibility import VisibilityItem, evaluate

FIXTURE = Path(__file__).parent / "fixtures" / "intake_visibility_cases.json"

_CASES: list[dict[str, Any]] = json.loads(FIXTURE.read_text())["cases"]


def _items(case: dict[str, Any]) -> list[VisibilityItem]:
    return [
        VisibilityItem(
            key=item["key"],
            rule=VisibleWhen(**item["visible_when"]) if "visible_when" in item else None,
            instrument_items=item.get("instrument_items"),
        )
        for item in case["items"]
    ]


@pytest.mark.parametrize("case", _CASES, ids=[case["name"] for case in _CASES])
def test_the_rule_table(case: dict[str, Any]) -> None:
    assert evaluate(_items(case), case["answers"]) == case["visible"]


class TestTheTableCoversTheSchema:
    """An operator nobody wrote a case for is an operator nobody tested."""

    def test_every_operator_appears(self) -> None:
        used = {
            item["visible_when"]["op"]
            for case in _CASES
            for item in case["items"]
            if "visible_when" in item
        }
        assert used == ANSWER_OPS | INSTRUMENT_OPS

    def test_every_case_names_every_item(self) -> None:
        """``evaluate`` answers for the whole form, so the table has to too."""
        for case in _CASES:
            keys = {item["key"] for item in case["items"]}
            assert set(case["visible"]) == keys, case["name"]

    def test_both_answers_appear_for_every_operator(self) -> None:
        """Each operator is shown holding somewhere and not holding somewhere."""
        held: dict[str, set[bool]] = {}
        for case in _CASES:
            for item in case["items"]:
                rule = item.get("visible_when")
                if rule is not None:
                    held.setdefault(rule["op"], set()).add(case["visible"][item["key"]])
        assert {op for op, seen in held.items() if seen != {True, False}} == set()


class TestWhatTheTableWouldNotShowClearly:
    """Properties, rather than cases, where a case would read as a riddle."""

    def test_a_form_with_no_questions_answers_nothing(self) -> None:
        assert evaluate([], {}) == {}

    def test_a_true_is_not_a_one(self) -> None:
        """A yes-or-no compared against a number is not equal to it."""
        rule = VisibleWhen(item_key="agrees", op="eq", value=1)
        items = [VisibilityItem(key="agrees"), VisibilityItem(key="detail", rule=rule)]
        assert evaluate(items, {"agrees": {"yes": True}})["detail"] is False

    def test_a_number_is_not_a_date(self) -> None:
        rule = VisibleWhen(item_key="started", op="gte", value=3)
        items = [VisibilityItem(key="started"), VisibilityItem(key="detail", rule=rule)]
        assert evaluate(items, {"started": {"value": "2020-01-01"}})["detail"] is False

    def test_a_measure_with_no_size_settles_nothing(self) -> None:
        """Without the item count there is no way to know it is finished."""
        rule = VisibleWhen(item_key="phq2", op="score_gte", value=1)
        items = [VisibilityItem(key="phq2"), VisibilityItem(key="phq9", rule=rule)]
        answers = {"phq2": {"item_scores": {"1": 3, "2": 3}}}
        assert evaluate(items, answers)["phq9"] is False

    def test_an_item_number_outside_the_measure_settles_nothing(self) -> None:
        rule = VisibleWhen(item_key="phq2", op="item_gte", value={"item": 9, "value": 1})
        items = [
            VisibilityItem(key="phq2", instrument_items=2),
            VisibilityItem(key="screen", rule=rule),
        ]
        answers = {"phq2": {"item_scores": {"1": 3, "2": 3}}}
        assert evaluate(items, answers)["screen"] is False

    def test_a_measure_answered_with_something_that_is_not_a_score(self) -> None:
        rule = VisibleWhen(item_key="phq2", op="score_gte", value=1)
        items = [
            VisibilityItem(key="phq2", instrument_items=2),
            VisibilityItem(key="phq9", rule=rule),
        ]
        answers = {"phq2": {"item_scores": {"1": 3, "2": "three"}}}
        assert evaluate(items, answers)["phq9"] is False

    def test_an_answer_that_is_not_a_mapping_is_not_an_answer(self) -> None:
        """What a repository hands back is always a mapping; a browser's is not."""
        rule = VisibleWhen(item_key="drinks", op="answered")
        items = [VisibilityItem(key="drinks"), VisibilityItem(key="detail", rule=rule)]
        assert evaluate(items, {"drinks": None})["detail"] is False
