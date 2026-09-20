# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether a form is finished, and what is still holding it up.

Two things are being pinned here.

**The arithmetic.** A required question with no acceptable answer is
missing; an optional one never is; a heading collects nothing and so never
is either. ``missing`` comes back in the order the form asks, because a
client sends somebody to the first thing it names.

**What a rule does to that arithmetic.** A question this patient is not
shown is neither required nor missing, and is named in ``hidden`` instead
so that submitting knows not to file what was typed into it. The rules
themselves are exercised against their own table in
``test_intake_visibility.py``; what is here is the consequence.
"""

from __future__ import annotations

from typing import Any

from app.intake.completion import CompletionItem, assess
from app.intake.items import validate_item_config

_PHQ9_COMPLETE = {str(i): 1 for i in range(1, 10)}

_CHOICES = {
    "options": [
        {"key": "yes", "label": "Yes"},
        {"key": "no", "label": "No"},
    ]
}


def _item(
    item_id: str,
    key: str,
    item_type: str,
    *,
    required: bool = True,
    **settings: Any,
) -> CompletionItem:
    return CompletionItem(
        item_id=item_id,
        key=key,
        required=required,
        config=validate_item_config(item_type, dict(settings)),
    )


def _default_packet() -> list[CompletionItem]:
    """The four questions every practice starts with, in order."""
    return [
        _item("i1", "demographics", "demographics"),
        _item("i2", "reason", "reason"),
        _item("i3", "phq9", "instrument", code="phq9"),
        _item("i4", "gad7", "instrument", code="gad7"),
    ]


def _branching_packet() -> list[CompletionItem]:
    """A yes-or-no that opens a required follow-up, and nothing else."""
    return [
        _item("i1", "substances", "yes_no"),
        _item(
            "i2",
            "which",
            "free_text",
            visible_when={"item_key": "substances", "op": "eq", "value": True},
        ),
    ]


class TestAQuestionNobodyWasShown:
    def test_it_does_not_hold_the_form_up(self) -> None:
        result = assess(_branching_packet(), {"substances": {"yes": False}})
        assert result.complete is True
        assert result.missing == []

    def test_it_is_named_as_hidden_instead(self) -> None:
        result = assess(_branching_packet(), {"substances": {"yes": False}})
        assert result.hidden == ["i2"]

    def test_the_same_question_is_required_once_it_is_shown(self) -> None:
        result = assess(_branching_packet(), {"substances": {"yes": True}})
        assert result.complete is False
        assert result.missing == ["i2"]
        assert result.hidden == []

    def test_an_unanswered_trigger_leaves_only_itself_outstanding(self) -> None:
        result = assess(_branching_packet(), {})
        assert result.missing == ["i1"]
        assert result.hidden == ["i2"]

    def test_an_answer_given_before_it_was_hidden_does_not_finish_it(self) -> None:
        """The form is complete because nobody is asked, not because it is answered."""
        answers = {"substances": {"yes": False}, "which": {"text": "Wine, most nights."}}
        result = assess(_branching_packet(), answers)
        assert result.complete is True
        assert result.hidden == ["i2"]


class TestTheSeamIsReallyConsulted:
    """Swap the evaluator and completion changes — so it is a seam."""

    def test_hiding_everything_completes_an_empty_form(self) -> None:
        result = assess(
            _default_packet(), {}, visibility=lambda items, _answers: {i.key: False for i in items}
        )
        assert result.complete is True
        assert result.missing == []
        assert result.hidden == ["i1", "i2", "i3", "i4"]

    def test_showing_everything_is_what_a_packet_with_no_rules_does(self) -> None:
        shown = assess(
            _default_packet(), {}, visibility=lambda items, _answers: {i.key: True for i in items}
        )
        assert shown.missing == assess(_default_packet(), {}).missing


class TestProgressOnTheDefaultPacket:
    def test_an_untouched_form_is_missing_every_question(self) -> None:
        result = assess(_default_packet(), {})
        assert result.complete is False
        assert result.missing == ["i1", "i2", "i3", "i4"]

    def test_a_partly_filled_form_names_exactly_what_is_left(self) -> None:
        answers = {
            "demographics": {"name_confirmed": True, "dob_confirmed": True},
            "phq9": {"item_scores": dict(_PHQ9_COMPLETE)},
        }
        result = assess(_default_packet(), answers)
        assert result.complete is False
        assert result.missing == ["i2", "i4"]

    def test_a_fully_answered_form_is_complete(self) -> None:
        answers = {
            "demographics": {"name_confirmed": True, "dob_confirmed": False},
            "reason": {"text": "Panic at work."},
            "phq9": {"item_scores": dict(_PHQ9_COMPLETE)},
            "gad7": {"item_scores": {str(i): 1 for i in range(1, 8)}},
        }
        result = assess(_default_packet(), answers)
        assert result.complete is True
        assert result.missing == []

    def test_missing_is_in_the_order_the_form_asks(self) -> None:
        answers = {"reason": {"text": "Panic at work."}}
        assert assess(_default_packet(), answers).missing == ["i1", "i3", "i4"]


class TestWhatIsNeverMissing:
    def test_an_optional_question_never_holds_the_form_up(self) -> None:
        items = [_item("i1", "note", "free_text", required=False)]
        assert assess(items, {}).complete is True

    def test_a_heading_collects_nothing(self) -> None:
        items = [_item("i1", "about_you", "section", title="About you")]
        assert assess(items, {}).complete is True

    def test_a_paragraph_collects_nothing(self) -> None:
        items = [_item("i1", "intro", "instructions", body_markdown="Please read this.")]
        assert assess(items, {}).complete is True

    def test_a_form_with_no_questions_is_complete(self) -> None:
        assert assess([], {}).complete is True


class TestAnAnswerHasToBeValid:
    """Present is not the same as answered."""

    def test_a_value_the_question_refuses_leaves_it_missing(self) -> None:
        items = [_item("i1", "drinks", "single_choice", **_CHOICES)]
        result = assess(items, {"drinks": {"key": "sometimes"}})
        assert result.missing == ["i1"]

    def test_a_partial_measure_leaves_it_missing(self) -> None:
        items = [_item("i1", "phq9", "instrument", code="phq9")]
        partial = {str(i): 1 for i in range(1, 9)}
        assert assess(items, {"phq9": {"item_scores": partial}}).missing == ["i1"]


class TestAnItemThatNoLongerParses:
    """A stored question whose settings are broken holds the form up.

    It cannot be answered as it stands, so reporting it as answerable would
    let somebody hand in a form with a question nobody could have answered.
    """

    def test_a_required_unparseable_item_is_missing(self) -> None:
        items = [CompletionItem(item_id="i1", key="broken", required=True, config=None)]
        result = assess(items, {"broken": {"text": "anything"}})
        assert result.complete is False
        assert result.missing == ["i1"]

    def test_an_optional_unparseable_item_is_not(self) -> None:
        items = [CompletionItem(item_id="i1", key="broken", required=False, config=None)]
        assert assess(items, {}).complete is True
