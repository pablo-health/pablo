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
    LABEL_REQUIRED_ITEM_TYPES,
    ItemConfigError,
    ItemDraft,
    stored_config,
    validate_item_config,
    validate_item_list,
)
from app.intake.rules import DISPLAY_ONLY_TARGETS


def _draft(key: str, item_type: str, **config: object) -> ItemDraft:
    """One item as the editor sends it, with a question where one is needed.

    The label is filled in here rather than by every caller because almost
    none of these tests is about the wording — they are about settings and
    rules, and a form that cannot be published for a missing question would
    hide what they are checking. The tests that ARE about it build their own
    drafts; see :class:`TestEveryQuestionCarriesItsWording`.
    """
    label = "How have you been?" if item_type in LABEL_REQUIRED_ITEM_TYPES else None
    return ItemDraft(key=key, item_type=item_type, label=label, config=config)


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


class TestAMeasureThePracticeHasToBeLicensedFor:
    """The publish-time gate on a use-restricted measure.

    Every restricted instrument in the registry today is a catalogue entry
    with no wording, so none of them can reach a form and none of them can
    exercise this. The gate is therefore driven by marking one that CAN
    reach a form restricted for the length of a test — which is exactly the
    state the registry enters the day a restricted instrument's items are
    added, and the point of testing it now rather than then.
    """

    @pytest.fixture
    def restricted_gad7(self, monkeypatch: pytest.MonkeyPatch) -> str:
        from dataclasses import replace  # noqa: PLC0415

        from app.outcome_measures.instruments import INSTRUMENT_REGISTRY  # noqa: PLC0415

        monkeypatch.setitem(
            INSTRUMENT_REGISTRY,
            "gad7",
            replace(INSTRUMENT_REGISTRY["gad7"], rights="attestation_required"),
        )
        monkeypatch.setattr("app.intake.items.RESTRICTED_INSTRUMENTS", frozenset({"gad7"}))
        return "gad7"

    def test_it_cannot_be_published_without_the_practice_recording_permission(
        self, restricted_gad7: str
    ) -> None:
        with pytest.raises(ItemConfigError, match="record your practice's permission"):
            validate_item_list(
                [_draft("anxiety", "instrument", code=restricted_gad7)],
                instrument_attested=lambda _code: False,
            )

    def test_the_refusal_names_the_measure(self, restricted_gad7: str) -> None:
        """The practice has to know which one to go and record."""
        with pytest.raises(ItemConfigError, match="GAD-7"):
            validate_item_list(
                [_draft("anxiety", "instrument", code=restricted_gad7)],
                instrument_attested=lambda _code: False,
            )

    def test_the_refusal_names_the_question(self, restricted_gad7: str) -> None:
        with pytest.raises(ItemConfigError, match=r"^anxiety:"):
            validate_item_list(
                [_draft("anxiety", "instrument", code=restricted_gad7)],
                instrument_attested=lambda _code: False,
            )

    def test_it_publishes_once_permission_is_recorded(self, restricted_gad7: str) -> None:
        parsed = validate_item_list(
            [_draft("anxiety", "instrument", code=restricted_gad7)],
            instrument_attested=lambda _code: True,
        )
        assert parsed[0].item_type == "instrument"

    def test_a_free_measure_is_never_gated(self, restricted_gad7: str) -> None:
        """Only the restricted set is asked about. PHQ-9 is not in it."""
        parsed = validate_item_list(
            [_draft("mood", "instrument", code="phq9")],
            instrument_attested=lambda _code: False,
        )
        assert parsed[0].item_type == "instrument"

    def test_a_draft_saves_with_no_licence_store_to_ask(self, restricted_gad7: str) -> None:
        """Saving a draft goes through the same function as publishing.

        A practice building a form before it has recorded permission is an
        ordinary half-done thing; publishing is where it has to be right.
        """
        parsed = validate_item_list([_draft("anxiety", "instrument", code=restricted_gad7)])
        assert parsed[0].item_type == "instrument"


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


class TestEveryQuestionCarriesItsWording:
    """Publishing is where a question a practice wrote has to have one.

    Nullable in the column and unset on a draft, because a practice writes a
    form over several sittings and the editor has to be able to save what it
    has. The refusal names the item, because the editor shows it beside that
    item and "which question" is the only thing a therapist needs from it.
    """

    @pytest.mark.parametrize("item_type", sorted(LABEL_REQUIRED_ITEM_TYPES))
    def test_a_question_the_practice_wrote_needs_one(self, item_type: str) -> None:
        with pytest.raises(ItemConfigError, match="mood: write the question"):
            validate_item_list([_unlabelled("mood", item_type)])

    @pytest.mark.parametrize("item_type", ["section", "instructions", "demographics", "reason"])
    def test_the_engine_words_its_own_questions(self, item_type: str) -> None:
        validate_item_list([_unlabelled("q", item_type)])

    def test_a_measure_is_asked_the_way_the_measure_asks_it(self) -> None:
        validate_item_list([_unlabelled("phq9", "instrument", code="phq9")])

    def test_a_consent_is_named_by_the_document_it_points_at(self) -> None:
        """Which already has a title, so a second one would be two to change."""
        validate_item_list([_unlabelled("consent", "consent_document", document_key="doc-1")])

    def test_a_label_on_one_of_those_is_an_override_rather_than_a_refusal(self) -> None:
        validate_item_list(
            [ItemDraft(key="reason", item_type="reason", label="Why now?", config={})]
        )

    def test_whitespace_is_not_a_question(self) -> None:
        item = _unlabelled("mood", "free_text")
        item.label = "   "
        with pytest.raises(ItemConfigError, match="write the question"):
            validate_item_list([item])

    def test_help_text_is_never_required(self) -> None:
        parsed = validate_item_list([_draft("mood", "free_text")])
        assert len(parsed) == 1


def _unlabelled(key: str, item_type: str, **config: object) -> ItemDraft:
    """One item with no wording on it at all."""
    return ItemDraft(key=key, item_type=item_type, label=None, config=_settings(item_type, config))


def _settings(item_type: str, given: dict[str, object]) -> dict[str, object]:
    """Whatever the type needs beyond its wording, so the label is what fails."""
    defaults: dict[str, dict[str, object]] = {
        "section": {"title": "About you"},
        "instructions": {"body_markdown": "Take your time."},
        "single_choice": {"options": [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}]},
        "multi_choice": {"options": [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}]},
        "scale": {"min": 0, "max": 10, "min_label": "low", "max_label": "high"},
    }
    return {**defaults.get(item_type, {}), **given}


class TestStoredConfig:
    def test_a_mapping_comes_back_as_itself(self) -> None:
        assert stored_config({"code": "phq9"}) == {"code": "phq9"}

    def test_anything_else_reads_as_empty(self) -> None:
        """A row written by an older editor is a normal thing to find."""
        assert stored_config(None) == {}
        assert stored_config("not a mapping") == {}
