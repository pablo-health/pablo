# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The starter documents, and how a form's transcription answer is read.

Every starter is held to what a practice's own document is held to — it
parses through :mod:`app.intake.documents` and its digest is pinned — plus
what the copy guide asks of anything a client reads.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest
from app.intake.documents import (
    canonical_text,
    content_digest,
    fill_practice_values,
    render_html,
)
from app.intake.items import ItemDraft, validate_item_list
from app.intake.starters import (
    AI_TOOLS_CONSENT,
    AI_TOOLS_DOCUMENT_ITEM_KEY,
    AI_TRANSCRIPTION_ITEM_KEY,
    STARTERS,
    Starter,
    check_starter,
    clear_registered_intake_starters,
    intake_starters,
    register_intake_starter,
    starter,
)
from app.services.intake_form_ai_consent import signed_on, transcription_answer

if TYPE_CHECKING:
    from collections.abc import Iterator

#: The digest of each starter's words. A change here is a change to what
#: clients are asked to agree to, so it is made on purpose or not at all.
_PINNED_DIGESTS = {
    "ai_tools_consent": "bc6a0ce5144f208bb1c0baca2cd9d9bf5610489ffaea62b48b54dd93879b4028",
}

_VALUES = {"audio_retention_days": "365"}

_AI_TOOLS_BODY = AI_TOOLS_CONSENT.body_markdown or ""


@pytest.mark.parametrize("chosen", STARTERS, ids=lambda s: s.key)
class TestEveryStarter:
    def test_it_parses_into_a_document(self, chosen: Starter) -> None:
        body = chosen.body_markdown
        assert body is not None
        html = render_html(fill_practice_values(body, _VALUES))
        assert html.startswith("<h2>")
        assert canonical_text(body)

    def test_its_digest_is_pinned(self, chosen: Starter) -> None:
        assert chosen.body_markdown is not None
        assert content_digest(chosen.body_markdown) == _PINNED_DIGESTS[chosen.key]

    def test_its_questions_can_go_on_a_form_after_the_document(self, chosen: Starter) -> None:
        """The items the editor adds publish, given a published document."""
        assert chosen.document_item_key is not None
        items = [
            ItemDraft(
                key=chosen.document_item_key,
                item_type="consent_document",
                config={"document_key": "the-practices-copy"},
            ),
            *chosen.questions,
        ]
        validate_item_list(items, published_document=lambda _key: "version-1")

    def test_it_passes_the_check_a_registered_one_does(self, chosen: Starter) -> None:
        check_starter(chosen)

    def test_it_can_be_found_by_its_key(self, chosen: Starter) -> None:
        assert starter(chosen.key) is chosen


# --- starters a deployment registers ------------------------------------------

_POLICY = Starter(
    key="cancellation_policy",
    title="Cancellation policy",
    body_markdown="# Cancellation policy\n\nPlease tell us a day ahead if you cannot come.",
    document_item_key="cancellation_policy",
    questions=(
        ItemDraft(
            key="cancellation_reminder",
            item_type="yes_no",
            label="Would you like a reminder the day before?",
        ),
    ),
)

_HISTORY = Starter(
    key="history",
    title="Your history",
    questions=(
        ItemDraft(key="history_section", item_type="section", config={"title": "Your history"}),
        ItemDraft(
            key="history_previous_care",
            item_type="free_text",
            label="Have you seen a counselor or therapist before?",
        ),
    ),
)


@pytest.fixture
def registry() -> Iterator[None]:
    clear_registered_intake_starters()
    yield
    clear_registered_intake_starters()


@pytest.mark.usefixtures("registry")
class TestRegisteringAStarter:
    def test_with_nothing_registered_only_the_built_ins_are_listed(self) -> None:
        assert intake_starters() == STARTERS

    def test_registered_ones_follow_the_built_ins_in_the_order_registered(self) -> None:
        register_intake_starter(_POLICY)
        register_intake_starter(_HISTORY)
        assert intake_starters() == (*STARTERS, _POLICY, _HISTORY)

    def test_a_registered_one_is_found_by_its_key(self) -> None:
        register_intake_starter(_POLICY)
        assert starter("cancellation_policy") is _POLICY

    def test_it_keeps_its_questions(self) -> None:
        register_intake_starter(_POLICY)
        found = starter("cancellation_policy")
        assert found is not None
        assert [q.key for q in found.questions] == ["cancellation_reminder"]

    def test_a_set_of_questions_without_a_document_is_a_starter(self) -> None:
        register_intake_starter(_HISTORY)
        assert starter("history") is _HISTORY

    def test_a_key_already_registered_is_refused(self) -> None:
        register_intake_starter(_POLICY)
        with pytest.raises(ValueError, match="already a starter named"):
            register_intake_starter(replace(_POLICY, title="Another title"))
        assert intake_starters() == (*STARTERS, _POLICY)

    def test_a_built_in_key_is_refused(self) -> None:
        with pytest.raises(ValueError, match="already a starter named"):
            register_intake_starter(replace(_POLICY, key=AI_TOOLS_CONSENT.key))

    def test_a_title_already_taken_is_refused(self) -> None:
        """Adopting reuses the practice's document of the same title."""
        with pytest.raises(ValueError, match="already a starter titled"):
            register_intake_starter(replace(_POLICY, title=AI_TOOLS_CONSENT.title))

    @pytest.mark.parametrize(
        ("broken", "message"),
        [
            (replace(_POLICY, key="Cancellation Policy"), "cannot be a starter's key"),
            (replace(_POLICY, title="  "), "needs a title"),
            (replace(_POLICY, body_markdown="---\n"), "no words"),
            (replace(_POLICY, document_item_key=None), "needs a consent item"),
            (replace(_HISTORY, document_item_key="history"), "needs a document to point at"),
            (replace(_HISTORY, questions=()), "a document or a question"),
            (
                replace(_POLICY, document_item_key="cancellation_reminder"),
                "Two questions are both named",
            ),
            (
                replace(
                    _HISTORY,
                    questions=(ItemDraft(key="pick", item_type="single_choice", label="Pick"),),
                ),
                "pick",
            ),
        ],
        ids=[
            "bad-key",
            "blank-title",
            "empty-document",
            "document-without-item",
            "item-without-document",
            "nothing-at-all",
            "item-key-collides",
            "question-that-cannot-publish",
        ],
    )
    def test_one_that_could_not_go_on_a_form_is_refused(
        self, broken: Starter, message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            register_intake_starter(broken)
        assert intake_starters() == STARTERS


class TestTheAiToolsConsent:
    def _words(self) -> list[str]:
        return canonical_text(fill_practice_values(_AI_TOOLS_BODY, _VALUES)).split()

    def test_it_is_under_250_words(self) -> None:
        assert len(self._words()) < 250

    def test_no_number_is_written_into_it(self) -> None:
        """The retention period comes from the practice's setting."""
        assert not re.search(r"\d", _AI_TOOLS_BODY)
        assert "{{audio_retention_days}}" in _AI_TOOLS_BODY

    def test_the_practices_retention_period_is_shown(self) -> None:
        for days in ("30", "365"):
            html = render_html(fill_practice_values(_AI_TOOLS_BODY, {"audio_retention_days": days}))
            assert f"keeps session audio for {days} days" in html

    def test_it_uses_no_gendered_pronouns(self) -> None:
        gendered = {"he", "him", "his", "she", "her", "hers", "himself", "herself"}
        assert not gendered & {w.strip(".,()").lower() for w in self._words()}

    def test_the_question_is_required_with_two_answers(self) -> None:
        [question] = AI_TOOLS_CONSENT.questions
        assert question.key == AI_TRANSCRIPTION_ITEM_KEY
        assert question.item_type == "single_choice"
        assert question.required is True
        options = question.config["options"]
        assert isinstance(options, list)
        assert [o["label"] for o in options] == ["I consent", "I do not consent"]


class TestFillingPracticeValues:
    def test_a_known_name_is_filled(self) -> None:
        assert fill_practice_values("{{ audio_retention_days }} days", _VALUES) == "365 days"

    def test_an_unknown_name_stays_as_written(self) -> None:
        assert fill_practice_values("{{owner_email}}", {"owner_email": "x"}) == "{{owner_email}}"

    def test_a_name_with_no_value_stays_as_written(self) -> None:
        assert fill_practice_values("{{audio_retention_days}}", {}) == "{{audio_retention_days}}"

    def test_the_value_is_escaped_by_the_render_like_any_text(self) -> None:
        html = render_html(
            fill_practice_values("{{audio_retention_days}}", {"audio_retention_days": "<b>"})
        )
        assert "<b>" not in html


# --- reading a submitted form --------------------------------------------------

_ITEMS: list[dict[str, object]] = [
    {"id": "doc-item", "key": AI_TOOLS_DOCUMENT_ITEM_KEY, "item_type": "consent_document"},
    {"id": "choice-item", "key": AI_TRANSCRIPTION_ITEM_KEY, "item_type": "single_choice"},
]


class TestTheTranscriptionAnswer:
    @pytest.mark.parametrize(
        ("key", "decision"), [("consent", "consented"), ("decline", "declined")]
    )
    def test_each_answer_maps_to_a_decision(self, key: str, decision: str) -> None:
        assert transcription_answer(_ITEMS, {"choice-item": {"key": key}}) == decision

    def test_an_unanswered_question_is_none(self) -> None:
        assert transcription_answer(_ITEMS, {}) is None

    def test_an_answer_the_starter_does_not_offer_is_none(self) -> None:
        assert transcription_answer(_ITEMS, {"choice-item": {"key": "maybe"}}) is None

    def test_a_form_without_the_question_is_none(self) -> None:
        assert transcription_answer(_ITEMS[:1], {"choice-item": {"key": "consent"}}) is None

    def test_a_question_of_another_type_under_the_key_is_ignored(self) -> None:
        items: list[dict[str, object]] = [
            {"id": "choice-item", "key": AI_TRANSCRIPTION_ITEM_KEY, "item_type": "free_text"}
        ]
        assert transcription_answer(items, {"choice-item": {"key": "consent"}}) is None


class TestTheDayItTakesEffect:
    def test_the_day_the_document_was_signed(self) -> None:
        signatures = [
            {"item_id": "doc-item", "signed_at": datetime(2026, 10, 2, 15, tzinfo=UTC)},
            {"item_id": "doc-item", "signed_at": datetime(2026, 10, 3, 9, tzinfo=UTC)},
            {"item_id": "other", "signed_at": datetime(2026, 9, 1, tzinfo=UTC)},
        ]
        assert signed_on(_ITEMS, signatures) == date(2026, 10, 2)

    def test_none_when_nothing_was_signed(self) -> None:
        assert signed_on(_ITEMS, []) is None
