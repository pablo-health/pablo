# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What counts as answering a question that asks for a file.

Pure functions, so these are the cheapest tests in the feature and the ones
that pin the rule: a card is answered when every side it asked for has
arrived exactly once, and a document request when at least one file has.

Everything here is about SHAPE. Whether a document exists, and whose it is,
is settled by the artifact route before an id ever reaches one of these
values — see ``test_patient_intake_artifacts_api.py``. A validator that
tried to answer it would need a database and would stop being pure, which
is what lets completion be computed on read.
"""

from __future__ import annotations

import pytest
from app.intake.answers import (
    AnswerError,
    is_answered,
    validate_answer,
    validate_document_request,
    validate_insurance_card,
)
from app.intake.items import (
    DocumentRequestConfig,
    InsuranceCardConfig,
    ItemConfigError,
    validate_item_config,
)

_DOC_A = "aaaaaaaa-0000-4000-8000-000000000001"
_DOC_B = "aaaaaaaa-0000-4000-8000-000000000002"


def _card(sides: str = "both") -> InsuranceCardConfig:
    config = validate_item_config("insurance_card", {"sides": sides})
    assert isinstance(config, InsuranceCardConfig)
    return config


def _request(**config: object) -> DocumentRequestConfig:
    parsed = validate_item_config("document_request", config)
    assert isinstance(parsed, DocumentRequestConfig)
    return parsed


def _sides(*entries: tuple[str, str]) -> dict[str, object]:
    return {"documents": [{"document_id": doc, "side": side} for doc, side in entries]}


class TestTheCardConfig:
    def test_both_sides_is_the_default(self) -> None:
        assert _card().sides == "both"

    def test_the_typed_fields_are_off_unless_asked_for(self) -> None:
        """A practice that wants the plan typed as well says so."""
        assert _card().collect_fields is False
        config = validate_item_config("insurance_card", {"collect_fields": True})
        assert isinstance(config, InsuranceCardConfig)
        assert config.collect_fields is True


class TestTheDocumentRequestConfig:
    def test_it_accepts_the_three_types_by_default(self) -> None:
        """The common case configures nothing."""
        assert _request().accept == ["application/pdf", "image/jpeg", "image/png"]

    def test_a_practice_can_narrow_it(self) -> None:
        assert _request(accept=["application/pdf"]).accept == ["application/pdf"]

    def test_a_type_the_storage_layer_would_refuse_cannot_be_published(self) -> None:
        """Otherwise a practice could ask for a file nobody could send."""
        with pytest.raises(ItemConfigError) as exc:
            validate_item_config("document_request", {"accept": ["application/zip"]})
        assert "application/zip" in str(exc.value)

    def test_the_same_type_twice_is_refused(self) -> None:
        with pytest.raises(ItemConfigError):
            validate_item_config("document_request", {"accept": ["image/png", "image/png"]})

    def test_a_blank_form_is_optional(self) -> None:
        assert _request().blank_form_id is None
        assert _request(blank_form_id="form-1").blank_form_id == "form-1"


class TestAnsweringACard:
    def test_both_sides_is_answered(self) -> None:
        validate_insurance_card(_sides((_DOC_A, "front"), (_DOC_B, "back")), _card())

    def test_the_order_does_not_matter(self) -> None:
        """Somebody may photograph the back first."""
        validate_insurance_card(_sides((_DOC_B, "back"), (_DOC_A, "front")), _card())

    def test_no_sides_is_not(self) -> None:
        assert not is_answered(_card(), {"documents": []})

    def test_one_side_of_two_is_not(self) -> None:
        with pytest.raises(AnswerError) as exc:
            validate_insurance_card(_sides((_DOC_A, "front")), _card())
        assert "back" in str(exc.value)

    def test_a_front_only_question_is_answered_by_a_front(self) -> None:
        validate_insurance_card(_sides((_DOC_A, "front")), _card("front"))

    def test_and_refuses_a_back_it_never_asked_for(self) -> None:
        with pytest.raises(AnswerError):
            validate_insurance_card(_sides((_DOC_A, "front"), (_DOC_B, "back")), _card("front"))

    def test_two_photos_of_the_front_is_not_an_answer(self) -> None:
        """A card nobody can read the plan off is not a finished question."""
        with pytest.raises(AnswerError):
            validate_insurance_card(_sides((_DOC_A, "front"), (_DOC_B, "front")), _card())

    def test_an_entry_with_no_document_id_is_refused(self) -> None:
        with pytest.raises(AnswerError):
            validate_insurance_card({"documents": [{"side": "front"}]}, _card())

    def test_a_value_that_is_not_a_list_is_refused(self) -> None:
        with pytest.raises(AnswerError):
            validate_insurance_card({"documents": _DOC_A}, _card())


class TestAnsweringADocumentRequest:
    def test_one_file_is_enough(self) -> None:
        validate_document_request({"documents": [{"document_id": _DOC_A}]}, _request())

    def test_several_are_fine(self) -> None:
        """A practice asking for prior records may well be sent three."""
        validate_document_request(
            {"documents": [{"document_id": _DOC_A}, {"document_id": _DOC_B}]}, _request()
        )

    def test_none_is_not(self) -> None:
        with pytest.raises(AnswerError):
            validate_document_request({"documents": []}, _request())

    def test_a_missing_key_is_not(self) -> None:
        assert not is_answered(_request(), {})


class TestTheDispatch:
    def test_validate_answer_reaches_both(self) -> None:
        """They are no longer refused as unanswerable, which they used to be."""
        validate_answer(_card(), _sides((_DOC_A, "front"), (_DOC_B, "back")))
        validate_answer(_request(), {"documents": [{"document_id": _DOC_A}]})

    def test_a_non_mapping_value_is_refused_before_either(self) -> None:
        with pytest.raises(AnswerError):
            validate_answer(_card(), ["not", "a", "mapping"])
