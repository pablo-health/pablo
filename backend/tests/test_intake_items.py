# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a valid intake item is, and what a valid form made of them is.

Two layers, and they fail for different reasons. One item at a time
(:func:`validate_item_config`) catches a question whose own settings do not
work — a scale that runs backwards, a measure nobody can fill in. The whole
list (:func:`validate_item_list`) catches what only a list can be wrong
about: two questions with the same name, and a question that depends on an
answer nobody has given yet.

The error messages are asserted as well as the refusals. They are what the
editor shows beside the item, so a refusal that says the right thing in
schema terms and the wrong thing in a therapist's terms is still a bug.
"""

from __future__ import annotations

import pytest
from app.db.models import IntakeItemDefinitionRow
from app.intake.items import (
    DISPLAY_ONLY_ITEM_TYPES,
    ITEM_TYPES,
    ItemConfigError,
    ItemDraft,
    stored_config,
    validate_item_config,
    validate_item_list,
)
from app.intake.rules import DISPLAY_ONLY_TARGETS


def _draft(key: str, item_type: str, **config: object) -> ItemDraft:
    return ItemDraft(key=key, item_type=item_type, config=config)


class TestTheVocabularyMatchesTheColumn:
    def test_every_item_type_is_allowed_by_the_check_constraint(self) -> None:
        """A type the code offers and the column refuses is unstorable.

        The CHECK is written out by hand in the migration and mirrored on the
        model, so this is the pin that keeps a type added in one place from
        being rejected in the other.
        """
        constraint = next(
            c
            for c in IntakeItemDefinitionRow.__table__.constraints
            if c.name == "ck_intake_item_definitions_type"
        )
        allowed = {
            part.strip().strip("'")
            for part in str(constraint.sqltext).split("(", 1)[1].rsplit(")", 1)[0].split(",")
        }
        assert allowed == set(ITEM_TYPES)

    def test_display_only_types_are_the_same_set_on_both_sides(self) -> None:
        """``rules`` names them separately to avoid an import cycle."""
        assert DISPLAY_ONLY_ITEM_TYPES == DISPLAY_ONLY_TARGETS

    def test_an_unknown_type_is_refused(self) -> None:
        with pytest.raises(ItemConfigError, match="not a kind of question"):
            validate_item_config("mood_ring", {})


class TestOneItemAtATime:
    def test_the_fixed_types_take_no_configuration(self) -> None:
        for item_type in ("demographics", "reason", "emergency_contact", "guardian"):
            assert validate_item_config(item_type, {}).item_type == item_type

    def test_an_unexpected_setting_is_refused(self) -> None:
        with pytest.raises(ItemConfigError):
            validate_item_config("demographics", {"options": []})

    def test_a_scale_needs_a_top_above_its_bottom(self) -> None:
        with pytest.raises(ItemConfigError, match="not above the bottom"):
            validate_item_config(
                "scale", {"min": 5, "max": 2, "min_label": "low", "max_label": "high"}
            )

    def test_a_scale_that_runs_upwards_is_fine(self) -> None:
        config = validate_item_config(
            "scale", {"min": 0, "max": 10, "min_label": "None", "max_label": "Constant"}
        )
        assert config.item_type == "scale"

    def test_a_number_range_cannot_run_backwards(self) -> None:
        with pytest.raises(ItemConfigError, match="below the smallest"):
            validate_item_config("number", {"min": 10, "max": 1})

    def test_a_choice_question_needs_at_least_two_answers(self) -> None:
        with pytest.raises(ItemConfigError):
            validate_item_config("single_choice", {"options": [{"key": "a", "label": "A"}]})

    def test_two_answers_cannot_share_a_name(self) -> None:
        with pytest.raises(ItemConfigError, match="share the name"):
            validate_item_config(
                "multi_choice",
                {"options": [{"key": "a", "label": "A"}, {"key": "a", "label": "Also A"}]},
            )

    def test_a_multi_choice_cannot_require_more_than_it_offers(self) -> None:
        with pytest.raises(ItemConfigError, match="more answers are required"):
            validate_item_config(
                "multi_choice",
                {
                    "options": [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}],
                    "min": 3,
                },
            )

    def test_only_a_self_report_measure_can_be_asked(self) -> None:
        """``dire`` scores fine and is rated by a clinician, not a patient."""
        assert validate_item_config("instrument", {"code": "phq9"}).item_type == "instrument"
        with pytest.raises(ItemConfigError, match="not a measure a patient can fill in"):
            validate_item_config("instrument", {"code": "dire"})

    def test_an_unknown_measure_is_refused(self) -> None:
        with pytest.raises(ItemConfigError, match="not a measure"):
            validate_item_config("instrument", {"code": "made_up"})

    def test_a_section_needs_a_title_and_instructions_need_a_body(self) -> None:
        assert validate_item_config("section", {"title": "About you"}).item_type == "section"
        with pytest.raises(ItemConfigError):
            validate_item_config("section", {"title": ""})
        with pytest.raises(ItemConfigError):
            validate_item_config("instructions", {})

    def test_a_yes_no_question_may_ask_for_more(self) -> None:
        config = validate_item_config("yes_no", {"follow_up_label": "Tell us more"})
        assert config.item_type == "yes_no"


class TestTheWholeList:
    def test_a_form_needs_at_least_one_question(self) -> None:
        with pytest.raises(ItemConfigError, match="at least one question"):
            validate_item_list([])

    def test_two_questions_cannot_share_a_name(self) -> None:
        with pytest.raises(ItemConfigError, match="both named"):
            validate_item_list([_draft("reason", "reason"), _draft("reason", "free_text")])

    def test_a_name_has_to_read_like_a_name(self) -> None:
        with pytest.raises(ItemConfigError, match="cannot be a question's name"):
            validate_item_list([_draft("Reason For Visit", "reason")])

    def test_a_consent_document_has_to_name_a_document(self) -> None:
        with pytest.raises(ItemConfigError, match="document_key"):
            validate_item_list([_draft("consent", "consent_document")])

    def test_a_consent_document_passes_with_no_lookup_to_ask(self) -> None:
        """The editor's own validation has no document store to consult.

        Saving a draft goes through the same function as publishing, and a
        practice picking a document it has not published yet is a normal
        thing to have half-done.
        """
        parsed = validate_item_list([_draft("consent", "consent_document", document_key="doc-1")])
        assert parsed[0].item_type == "consent_document"

    def test_an_unpublished_document_cannot_be_asked_for(self) -> None:
        with pytest.raises(ItemConfigError, match="publish this document"):
            validate_item_list(
                [_draft("consent", "consent_document", document_key="doc-1")],
                published_document=lambda _key: None,
            )

    def test_a_published_document_can_be_asked_for(self) -> None:
        parsed = validate_item_list(
            [_draft("consent", "consent_document", document_key="doc-1")],
            published_document=lambda _key: "version-7",
        )
        assert parsed[0].item_type == "consent_document"

    def test_the_item_that_is_wrong_is_named(self) -> None:
        with pytest.raises(ItemConfigError, match=r"^how_bad:"):
            validate_item_list(
                [
                    _draft(
                        "how_bad",
                        "scale",
                        min=9,
                        max=1,
                        min_label="low",
                        max_label="high",
                    )
                ]
            )


class TestBranching:
    def _screener(self) -> ItemDraft:
        return _draft(
            "drinks",
            "single_choice",
            options=[{"key": "never", "label": "Never"}, {"key": "often", "label": "Often"}],
        )

    def test_a_rule_may_point_at_an_earlier_question(self) -> None:
        parsed = validate_item_list(
            [
                self._screener(),
                _draft(
                    "audit",
                    "free_text",
                    visible_when={"item_key": "drinks", "op": "eq", "value": "often"},
                ),
            ]
        )
        assert parsed[1].visible_when is not None

    def test_a_rule_may_not_point_forwards(self) -> None:
        with pytest.raises(ItemConfigError, match="nothing earlier on this form"):
            validate_item_list(
                [
                    _draft(
                        "audit",
                        "free_text",
                        visible_when={"item_key": "drinks", "op": "eq", "value": "often"},
                    ),
                    self._screener(),
                ]
            )

    def test_a_rule_may_not_name_an_answer_the_question_does_not_offer(self) -> None:
        with pytest.raises(ItemConfigError, match="does not offer"):
            validate_item_list(
                [
                    self._screener(),
                    _draft(
                        "audit",
                        "free_text",
                        visible_when={"item_key": "drinks", "op": "eq", "value": "daily"},
                    ),
                ]
            )

    def test_a_rule_may_not_point_at_a_heading(self) -> None:
        with pytest.raises(ItemConfigError, match="not a question"):
            validate_item_list(
                [
                    _draft("about", "section", title="About you"),
                    _draft(
                        "audit",
                        "free_text",
                        visible_when={"item_key": "about", "op": "answered"},
                    ),
                ]
            )

    def test_a_score_rule_reads_a_measures_total(self) -> None:
        validate_item_list(
            [
                _draft("phq9", "instrument", code="phq9"),
                _draft(
                    "follow_up",
                    "free_text",
                    visible_when={"item_key": "phq9", "op": "score_gte", "value": 10},
                ),
            ]
        )

    def test_a_score_rule_needs_a_measure_to_read(self) -> None:
        with pytest.raises(ItemConfigError, match="is not a measure"):
            validate_item_list(
                [
                    _draft("mood", "free_text"),
                    _draft(
                        "follow_up",
                        "free_text",
                        visible_when={"item_key": "mood", "op": "score_gte", "value": 10},
                    ),
                ]
            )

    def test_an_item_rule_reads_one_of_a_measures_items(self) -> None:
        """The PHQ-9's ninth item is the branch this operator exists for."""
        validate_item_list(
            [
                _draft("phq9", "instrument", code="phq9"),
                _draft(
                    "risk",
                    "free_text",
                    visible_when={
                        "item_key": "phq9",
                        "op": "item_gte",
                        "value": {"item": 9, "value": 1},
                    },
                ),
            ]
        )

    def test_an_item_rule_cannot_name_an_item_the_measure_does_not_have(self) -> None:
        with pytest.raises(ItemConfigError, match="has 7 items"):
            validate_item_list(
                [
                    _draft("gad7", "instrument", code="gad7"),
                    _draft(
                        "risk",
                        "free_text",
                        visible_when={
                            "item_key": "gad7",
                            "op": "item_gte",
                            "value": {"item": 9, "value": 1},
                        },
                    ),
                ]
            )

    def test_a_yes_no_rule_compares_against_yes_or_no(self) -> None:
        validate_item_list(
            [
                _draft("guardian_needed", "yes_no"),
                _draft(
                    "guardian",
                    "guardian",
                    visible_when={"item_key": "guardian_needed", "op": "eq", "value": True},
                ),
            ]
        )
        with pytest.raises(ItemConfigError, match="yes or no"):
            validate_item_list(
                [
                    _draft("guardian_needed", "yes_no"),
                    _draft(
                        "guardian",
                        "guardian",
                        visible_when={
                            "item_key": "guardian_needed",
                            "op": "eq",
                            "value": "yes",
                        },
                    ),
                ]
            )

    def test_answered_takes_no_value_and_the_others_need_one(self) -> None:
        with pytest.raises(ItemConfigError):
            validate_item_list(
                [
                    _draft("mood", "free_text"),
                    _draft(
                        "why",
                        "free_text",
                        visible_when={"item_key": "mood", "op": "answered", "value": "x"},
                    ),
                ]
            )
        with pytest.raises(ItemConfigError):
            validate_item_list(
                [
                    _draft("mood", "free_text"),
                    _draft("why", "free_text", visible_when={"item_key": "mood", "op": "eq"}),
                ]
            )


class TestStoredConfig:
    def test_a_mapping_comes_back_as_itself(self) -> None:
        assert stored_config({"code": "phq9"}) == {"code": "phq9"}

    def test_anything_else_reads_as_empty(self) -> None:
        """A row written by an older editor is a normal thing to find."""
        assert stored_config(None) == {}
        assert stored_config("not a mapping") == {}
