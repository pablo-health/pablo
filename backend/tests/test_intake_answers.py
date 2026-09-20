# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What each kind of intake question accepts as an answer, and what it refuses.

Every answerable item type gets a passing case and a failing one, because
either alone proves half of a validator: a check that only ever sees good
input passes when it is a no-op, and one that only ever sees bad input
passes when it rejects everything.

The messages are asserted in one place rather than everywhere. They are
what the person filling the form in reads, so what matters is that they
name the question and never repeat the answer back — checked once, against
the case most likely to leak one.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from app.intake.answers import AnswerError, is_answered, validate_answer
from app.intake.items import validate_item_config

_PHQ9_COMPLETE = {str(i): 1 for i in range(1, 10)}

_TWO_CHOICES = {
    "options": [
        {"key": "yes", "label": "Yes"},
        {"key": "no", "label": "No"},
        {"key": "unsure", "label": "Not sure"},
    ]
}


def _config(item_type: str, **settings: Any) -> Any:
    """One item's configuration, parsed the way publishing parses it."""
    return validate_item_config(item_type, dict(settings))


# (item_type, config settings, an answer that passes, an answer that fails)
_CASES: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]] = [
    (
        "demographics",
        {},
        {"name_confirmed": True, "dob_confirmed": True, "corrections": None},
        {"name_confirmed": True},
    ),
    (
        "reason",
        {},
        {"text": "Panic at work for about two months."},
        {"text": "   "},
    ),
    (
        "free_text",
        {"max_len": 20},
        {"text": "Sleeping badly."},
        {"text": "x" * 21},
    ),
    (
        "single_choice",
        dict(_TWO_CHOICES),
        {"key": "unsure"},
        {"key": "maybe"},
    ),
    (
        "multi_choice",
        {**_TWO_CHOICES, "min": 1, "max": 2},
        {"keys": ["yes", "no"]},
        {"keys": ["yes", "no", "unsure"]},
    ),
    (
        "yes_no",
        {"follow_up_label": "Tell us a little more"},
        {"yes": True, "follow_up": "Twice last month."},
        {"yes": True},
    ),
    (
        "scale",
        {"min": 0, "max": 10, "min_label": "Not at all", "max_label": "A great deal"},
        {"value": 7},
        {"value": 11},
    ),
    (
        "number",
        {"min": 0, "max": 40},
        {"value": 12},
        {"value": 41},
    ),
    (
        "date",
        {"past_only": True},
        {"value": "2020-01-31"},
        {"value": "not-a-date"},
    ),
    (
        "instrument",
        {"code": "phq9"},
        {"item_scores": dict(_PHQ9_COMPLETE)},
        {"item_scores": {"1": 1}},
    ),
    (
        "emergency_contact",
        {},
        {"name": "Ada", "phone": "+15555550100", "relationship": "Sister"},
        {"name": "Ada", "phone": "+15555550100"},
    ),
    (
        "guardian",
        {},
        {"name": "Ada", "relationship": "Parent"},
        {"name": "Ada"},
    ),
]

_IDS = [case[0] for case in _CASES]


@pytest.mark.parametrize(("item_type", "settings", "good", "bad"), _CASES, ids=_IDS)
class TestEveryValidatorAcceptsAndRefuses:
    """Both halves, for every answerable type."""

    def test_a_good_answer_is_accepted(
        self, item_type: str, settings: dict, good: dict, bad: dict
    ) -> None:
        assert bad is not None  # the failing case is exercised below
        validate_answer(_config(item_type, **settings), good)

    def test_a_bad_answer_is_refused(
        self, item_type: str, settings: dict, good: dict, bad: dict
    ) -> None:
        assert good is not None  # the passing case is exercised above
        with pytest.raises(AnswerError):
            validate_answer(_config(item_type, **settings), bad)


class TestDatesAreJudgedAgainstOneDay:
    """``today`` is passed in, so a form open overnight is judged once."""

    def test_a_future_date_is_refused_when_the_question_asks_for_the_past(self) -> None:
        config = _config("date", past_only=True)
        with pytest.raises(AnswerError):
            validate_answer(config, {"value": "2999-01-01"}, today=date(2026, 9, 20))

    def test_the_same_date_passes_when_today_is_later(self) -> None:
        config = _config("date", past_only=True)
        validate_answer(config, {"value": "2026-09-19"}, today=date(2026, 9, 20))

    def test_bounds_are_honoured(self) -> None:
        config = _config("date", min="2020-01-01", max="2020-12-31")
        validate_answer(config, {"value": "2020-06-15"}, today=date(2026, 9, 20))
        with pytest.raises(AnswerError):
            validate_answer(config, {"value": "2021-01-01"}, today=date(2026, 9, 20))


class TestTheShapeOfAnAnswer:
    def test_a_bare_value_is_not_an_answer(self) -> None:
        """Every answer is a mapping, so a field can be added to it later."""
        with pytest.raises(AnswerError):
            validate_answer(_config("yes_no"), True)

    def test_nothing_is_not_an_answer(self) -> None:
        assert not is_answered(_config("reason"), None)

    @pytest.mark.parametrize("item_type", ["section", "instructions"])
    def test_display_only_items_take_no_answer(self, item_type: str) -> None:
        settings = {"title": "About you"} if item_type == "section" else {"body_markdown": "Hello"}
        with pytest.raises(AnswerError):
            validate_answer(_config(item_type, **settings), {"text": "anything"})

    @pytest.mark.parametrize("item_type", ["insurance_card", "document_request"])
    def test_the_file_backed_types_are_refused_for_now(self, item_type: str) -> None:
        """Nothing stores a file yet, so they are refused rather than dangled."""
        config = _config(item_type)
        with pytest.raises(AnswerError):
            validate_answer(config, {"uploaded": True})


class TestMessagesAreSafeToShow:
    """They name the question and never repeat the answer back."""

    def test_a_refused_free_text_answer_is_not_echoed(self) -> None:
        secret = "I have been drinking every night since March."
        with pytest.raises(AnswerError) as exc:
            validate_answer(_config("free_text", max_len=10), {"text": secret})
        assert secret not in str(exc.value)

    def test_a_refused_choice_does_not_echo_what_was_sent(self) -> None:
        with pytest.raises(AnswerError) as exc:
            validate_answer(_config("single_choice", **_TWO_CHOICES), {"key": "i_drink_daily"})
        assert "i_drink_daily" not in str(exc.value)

    def test_a_follow_up_label_is_used_to_name_the_box(self) -> None:
        with pytest.raises(AnswerError) as exc:
            validate_answer(_config("yes_no", follow_up_label="Tell us more"), {"yes": True})
        assert "Tell us more" in str(exc.value)


class TestYesNoFollowUp:
    def test_a_no_needs_no_follow_up(self) -> None:
        validate_answer(_config("yes_no", follow_up_label="Tell us more"), {"yes": False})

    def test_a_follow_up_left_behind_on_a_no_is_ignored(self) -> None:
        """Changing the answer after typing should not need the box cleared."""
        config = _config("yes_no", follow_up_label="Tell us more")
        validate_answer(config, {"yes": False, "follow_up": "typed before changing my mind"})

    def test_a_question_with_no_follow_up_takes_none(self) -> None:
        validate_answer(_config("yes_no"), {"yes": True})


class TestMultiChoiceBounds:
    def test_the_fewest_required_is_enforced(self) -> None:
        config = _config("multi_choice", **{**_TWO_CHOICES, "min": 2})
        with pytest.raises(AnswerError):
            validate_answer(config, {"keys": ["yes"]})

    def test_the_same_answer_twice_is_refused(self) -> None:
        config = _config("multi_choice", **{**_TWO_CHOICES, "min": 1})
        with pytest.raises(AnswerError):
            validate_answer(config, {"keys": ["yes", "yes"]})

    def test_an_empty_list_passes_when_nothing_is_required(self) -> None:
        validate_answer(_config("multi_choice", **_TWO_CHOICES), {"keys": []})


class TestInstrumentAnswers:
    """Delegated to the registry that scores it, so the two cannot drift."""

    def test_a_partial_measure_is_not_an_answer(self) -> None:
        partial = {str(i): 1 for i in range(1, 9)}
        with pytest.raises(AnswerError) as exc:
            validate_answer(_config("instrument", code="phq9"), {"item_scores": partial})
        assert "9" in str(exc.value)

    def test_an_out_of_range_score_is_refused(self) -> None:
        scores = {**_PHQ9_COMPLETE, "3": 9}
        with pytest.raises(AnswerError):
            validate_answer(_config("instrument", code="phq9"), {"item_scores": scores})

    def test_an_unknown_item_key_is_refused(self) -> None:
        scores = {**_PHQ9_COMPLETE, "10": 1}
        with pytest.raises(AnswerError):
            validate_answer(_config("instrument", code="phq9"), {"item_scores": scores})


class TestBooleansAreNotNumbers:
    """``True`` is an ``int`` in Python and is not a scale point here."""

    def test_true_is_not_a_scale_answer(self) -> None:
        config = _config("scale", min=0, max=10, min_label="Not at all", max_label="A great deal")
        with pytest.raises(AnswerError):
            validate_answer(config, {"value": True})

    def test_true_is_not_a_number_answer(self) -> None:
        with pytest.raises(AnswerError):
            validate_answer(_config("number"), {"value": True})
