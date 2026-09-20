# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading a pre-packet intake body onto the default form's questions.

The command that moves these rows is proved end to end against a real
schema in ``tests_integration``. What is proved here is the one part of it
that is pure: which question each piece of a legacy payload answers, and
what happens to a body that is missing pieces — which is the normal case,
because the form gained fields over time and every body written before a
field existed is short of it.

The rule throughout is that a short body is read, not rejected. A
submission is a clinical record somebody filed; refusing to carry it across
because it predates a column would lose it.
"""

from __future__ import annotations

from app.bin.adopt_intake_submissions import _answers_from

_ITEMS = {
    "demographics": "item-demographics",
    "reason": "item-reason",
    "phq9": "item-phq9",
    "gad7": "item-gad7",
}

_PHQ9 = {str(i): 1 for i in range(1, 10)}
_GAD7 = {str(i): 2 for i in range(1, 8)}

_FULL_BODY = {
    "form_version": 1,
    "name_confirmed": True,
    "dob_confirmed": False,
    "corrections": "My date of birth is a day out.",
    "reason_text": "Panic before every shift.",
    "instruments": {"phq9": _PHQ9, "gad7": _GAD7},
    "outcome_measure_ids": {"phq9": "measure-1", "gad7": "measure-2"},
}


class TestAFullBody:
    def test_every_question_on_the_form_gets_its_answer(self) -> None:
        answers = _answers_from(_FULL_BODY, _ITEMS)
        assert set(answers) == set(_ITEMS.values())

    def test_the_attestation_carries_across_as_it_was_given(self) -> None:
        answers = _answers_from(_FULL_BODY, _ITEMS)
        assert answers["item-demographics"] == {
            "name_confirmed": True,
            "dob_confirmed": False,
            "corrections": "My date of birth is a day out.",
        }

    def test_the_reason_is_the_patients_own_words(self) -> None:
        answers = _answers_from(_FULL_BODY, _ITEMS)
        assert answers["item-reason"] == {"text": "Panic before every shift."}

    def test_each_measure_keeps_its_item_scores(self) -> None:
        answers = _answers_from(_FULL_BODY, _ITEMS)
        assert answers["item-phq9"] == {"item_scores": _PHQ9}
        assert answers["item-gad7"] == {"item_scores": _GAD7}

    def test_the_measure_ids_are_not_carried(self) -> None:
        """They point at outcome rows that already exist; nothing is re-scored."""
        answers = _answers_from(_FULL_BODY, _ITEMS)
        assert all("outcome_measure_ids" not in value for value in answers.values())


class TestAShortBody:
    def test_a_body_written_before_the_form_asked_reads_as_nothing_flagged(self) -> None:
        answers = _answers_from({"reason_text": "Sleep."}, _ITEMS)
        assert answers["item-demographics"] == {
            "name_confirmed": True,
            "dob_confirmed": True,
            "corrections": None,
        }

    def test_an_empty_reason_leaves_that_question_unanswered(self) -> None:
        """Rather than storing a blank, which the question would refuse."""
        answers = _answers_from({"reason_text": ""}, _ITEMS)
        assert "item-reason" not in answers

    def test_a_body_with_no_measures_carries_none(self) -> None:
        answers = _answers_from({"reason_text": "Sleep."}, _ITEMS)
        assert "item-phq9" not in answers
        assert "item-gad7" not in answers


class TestAFormThePracticeHasChanged:
    def test_a_question_the_practice_removed_has_nowhere_to_put_its_answer(self) -> None:
        """The submission still crosses; the removed question simply is not asked."""
        answers = _answers_from(_FULL_BODY, {"reason": "item-reason"})
        assert set(answers) == {"item-reason"}

    def test_a_measure_the_form_no_longer_asks_is_left_behind(self) -> None:
        answers = _answers_from(_FULL_BODY, {"phq9": "item-phq9"})
        assert set(answers) == {"item-phq9"}

    def test_a_form_with_none_of_these_questions_yields_nothing(self) -> None:
        """Which is what the command reports as skipped rather than adopted."""
        assert _answers_from(_FULL_BODY, {"how_did_you_hear": "item-x"}) == {}
