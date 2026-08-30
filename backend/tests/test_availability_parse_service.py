# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the natural-language availability-rule parse service.

Uses :class:`FakeStructuredLLMGateway` throughout -- no live LLM, matching
the note-import test pattern. Each test injects a queued/default
:class:`StructuredCompletion` shaped like what the real gateway would
return, and asserts the service's own validation (never the model's raw
output) is what reaches the caller.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from app.scheduling_engine.models.availability import RuleType
from app.services.availability_parse_service import (
    COVERED_RULE_TYPES,
    AvailabilityRuleParseService,
)
from app.services.structured_llm_gateway import (
    FakeStructuredLLMGateway,
    StructuredCompletion,
    StructuredOutputTruncatedError,
)
from app.settings import get_settings

REFERENCE_DATE = date(2026, 8, 26)  # Wednesday


def _service(gateway: FakeStructuredLLMGateway) -> AvailabilityRuleParseService:
    return AvailabilityRuleParseService(llm_gateway=gateway)


def _fake_service(response: dict[str, Any]) -> AvailabilityRuleParseService:
    gateway = FakeStructuredLLMGateway(default_response=StructuredCompletion(data=response))
    return _service(gateway)


class TestCoveredRuleTypes:
    def test_covers_exactly_the_eight_non_session_defaults_types(self) -> None:
        assert {
            "working_hours",
            "block_day_of_week",
            "block_time_range",
            "max_per_day",
            "buffer_before",
            "buffer_after",
            "block_date_range",
            "block_specific_dates",
        } == COVERED_RULE_TYPES

    def test_every_covered_type_is_a_valid_rule_type(self) -> None:
        for rule_type in COVERED_RULE_TYPES:
            RuleType(rule_type)  # raises ValueError if not a real enum member


class TestSingleProposal:
    def test_block_day_of_week_round_trips(self) -> None:
        response = {
            "proposals": [
                {
                    "rule_type": "block_day_of_week",
                    "enforcement": "hard",
                    "day_of_week": 4,
                    "human_summary": "No appointments on Fridays.",
                    "confidence": 0.95,
                }
            ],
            "could_not_parse": None,
            "exclusive": False,
        }
        result = _fake_service(response).parse("No appointments on Fridays")

        assert result.could_not_parse is None
        assert len(result.proposals) == 1
        proposal = result.proposals[0]
        assert proposal.rule_type == "block_day_of_week"
        assert proposal.enforcement == "hard"
        assert proposal.params == {"day_of_week": 4}


class TestMultipleProposals:
    def test_nine_to_five_weekdays_yields_five_proposals(self) -> None:
        response = {
            "proposals": [
                {
                    "rule_type": "working_hours",
                    "enforcement": "hard",
                    "day_of_week": day,
                    "start": "09:00",
                    "end": "17:00",
                    "human_summary": "Working 9-5.",
                    "confidence": 0.95,
                }
                for day in range(5)
            ],
            "could_not_parse": None,
            "exclusive": False,
        }
        result = _fake_service(response).parse("9 to 5 on weekdays")

        assert len(result.proposals) == 5
        assert [p.params["day_of_week"] for p in result.proposals] == [0, 1, 2, 3, 4]
        for proposal in result.proposals:
            assert proposal.rule_type == "working_hours"
            assert proposal.params["start"] == "09:00"
            assert proposal.params["end"] == "17:00"


class TestDateIntentResolution:
    def test_next_weekday_date_intent_resolves_to_block_specific_dates(self) -> None:
        response = {
            "proposals": [
                {
                    "rule_type": "block_specific_dates",
                    "enforcement": "hard",
                    "date_intent": {
                        "items": [{"day_of_week": 4, "modifier": "next"}],
                        "range": False,
                    },
                    "human_summary": "Blocked next Friday.",
                    "confidence": 0.95,
                }
            ],
            "could_not_parse": None,
            "exclusive": False,
        }
        result = _fake_service(response).parse("Block next Friday", REFERENCE_DATE)

        assert result.could_not_parse is None
        assert len(result.proposals) == 1
        proposal = result.proposals[0]
        assert proposal.rule_type == "block_specific_dates"
        assert proposal.params == {"dates": ["2026-09-04"]}

    def test_range_date_intent_resolves_to_block_date_range(self) -> None:
        response = {
            "proposals": [
                {
                    "rule_type": "block_date_range",
                    "enforcement": "hard",
                    "date_intent": {
                        "items": [{"day_of_week": 4}, {"day_of_week": 0}],
                        "range": True,
                    },
                    "human_summary": "Blocked Friday through Monday.",
                    "confidence": 0.95,
                }
            ],
            "could_not_parse": None,
            "exclusive": False,
        }
        result = _fake_service(response).parse("Block Friday through Monday", REFERENCE_DATE)

        assert result.could_not_parse is None
        assert len(result.proposals) == 1
        proposal = result.proposals[0]
        assert proposal.rule_type == "block_date_range"
        assert proposal.params == {"start_date": "2026-08-28", "end_date": "2026-08-31"}


class TestRejectedProposals:
    def _assert_rejected(self, response: dict[str, Any]) -> None:
        result = _fake_service(response).parse("some sentence")
        assert result.proposals == []
        assert result.could_not_parse
        assert result.could_not_parse.strip() != ""

    def test_rejects_a_date_proposal_that_resolves_its_own_dates(self) -> None:
        # The model must emit date_intent tokens, never a resolved date --
        # a proposal carrying start_date/end_date/dates directly is
        # rejected exactly like any other malformed proposal.
        self._assert_rejected(
            {
                "proposals": [
                    {
                        "rule_type": "block_date_range",
                        "enforcement": "hard",
                        "start_date": "2026-09-01",
                        "end_date": "2026-09-05",
                        "human_summary": "Blocked next week.",
                        "confidence": 0.95,
                    }
                ]
            }
        )

    def test_rejects_a_date_proposal_when_no_reference_date_is_given(self) -> None:
        result = _fake_service(
            {
                "proposals": [
                    {
                        "rule_type": "block_specific_dates",
                        "enforcement": "hard",
                        "date_intent": {
                            "items": [{"day_of_week": 4, "modifier": "next"}],
                            "range": False,
                        },
                        "human_summary": "Blocked next Friday.",
                        "confidence": 0.95,
                    }
                ]
            }
        ).parse("Block next Friday")

        assert result.proposals == []
        assert result.could_not_parse

    def test_rejects_unknown_rule_type(self) -> None:
        self._assert_rejected(
            {
                "proposals": [
                    {
                        "rule_type": "frobnicate_schedule",
                        "enforcement": "hard",
                        "human_summary": "??",
                        "confidence": 0.95,
                    }
                ]
            }
        )

    def test_rejects_day_of_week_out_of_range(self) -> None:
        self._assert_rejected(
            {
                "proposals": [
                    {
                        "rule_type": "block_day_of_week",
                        "enforcement": "hard",
                        "day_of_week": 7,
                        "human_summary": "Blocked.",
                        "confidence": 0.95,
                    }
                ]
            }
        )

    def test_rejects_end_before_start(self) -> None:
        self._assert_rejected(
            {
                "proposals": [
                    {
                        "rule_type": "block_time_range",
                        "enforcement": "hard",
                        "start": "17:00",
                        "end": "09:00",
                        "human_summary": "Blocked.",
                        "confidence": 0.95,
                    }
                ]
            }
        )

    def test_rejects_negative_buffer_minutes(self) -> None:
        self._assert_rejected(
            {
                "proposals": [
                    {
                        "rule_type": "buffer_before",
                        "enforcement": "hard",
                        "minutes": -5,
                        "human_summary": "Buffer.",
                        "confidence": 0.95,
                    }
                ]
            }
        )

    def test_rejects_max_per_day_zero(self) -> None:
        self._assert_rejected(
            {
                "proposals": [
                    {
                        "rule_type": "max_per_day",
                        "enforcement": "hard",
                        "max": 0,
                        "human_summary": "Limit.",
                        "confidence": 0.95,
                    }
                ]
            }
        )


class TestCouldNotParse:
    def test_empty_proposals_with_reason(self) -> None:
        response = {
            "proposals": [],
            "could_not_parse": "That mentions a specific date, which isn't supported here.",
            "exclusive": False,
        }
        result = _fake_service(response).parse("Block Dec 24th")

        assert result.proposals == []
        assert result.could_not_parse
        assert "date" in result.could_not_parse.lower()

    def test_empty_proposals_with_no_reason_still_gets_a_reason(self) -> None:
        response: dict[str, Any] = {"proposals": [], "could_not_parse": None, "exclusive": False}
        result = _fake_service(response).parse("asdkjfh")

        assert result.proposals == []
        assert result.could_not_parse
        assert result.could_not_parse.strip() != ""


class TestTruncationAndTransportErrors:
    def test_truncated_output_maps_to_could_not_parse(self) -> None:
        gateway = FakeStructuredLLMGateway(responses=[StructuredOutputTruncatedError("truncated")])
        result = _service(gateway).parse("a very long description")

        assert result.proposals == []
        assert result.could_not_parse

    def test_transport_error_propagates(self) -> None:
        gateway = FakeStructuredLLMGateway(responses=[RuntimeError("network down")])
        with pytest.raises(RuntimeError, match="network down"):
            _service(gateway).parse("no appointments on Fridays")


class TestPromptContract:
    def test_call_shape_and_prompt_content(self) -> None:
        response = {
            "proposals": [
                {
                    "rule_type": "block_day_of_week",
                    "enforcement": "hard",
                    "day_of_week": 4,
                    "human_summary": "No Fridays.",
                    "confidence": 0.95,
                }
            ],
            "could_not_parse": None,
            "exclusive": False,
        }
        gateway = FakeStructuredLLMGateway(default_response=StructuredCompletion(data=response))
        _service(gateway).parse("No appointments on Fridays")

        assert len(gateway.calls) == 1
        call = gateway.calls[0]
        assert call["temperature"] == 0.0
        assert call["thinking_budget"] == 0
        assert call["max_output_tokens"] == 2048
        assert call["user_prompt"] == "No appointments on Fridays"

        system_prompt = call["system_prompt"]
        for rule_type in COVERED_RULE_TYPES:
            assert rule_type in system_prompt
        assert "0=Monday" in system_prompt

        # Clock-free by construction: no date or timezone context anywhere.
        assert "today" not in system_prompt.lower()
        assert "timezone" not in system_prompt.lower()


class TestExclusivity:
    def test_only_phrasing_sets_exclusive_true(self) -> None:
        response = {
            "proposals": [
                {
                    "rule_type": "working_hours",
                    "enforcement": "hard",
                    "day_of_week": 0,
                    "start": "13:00",
                    "end": "15:00",
                    "human_summary": "Mondays 1-3.",
                    "confidence": 0.95,
                },
                {
                    "rule_type": "working_hours",
                    "enforcement": "hard",
                    "day_of_week": 1,
                    "start": "14:00",
                    "end": "16:00",
                    "human_summary": "Tuesdays 2-4.",
                    "confidence": 0.95,
                },
            ],
            "could_not_parse": None,
            "exclusive": True,
        }
        result = _fake_service(response).parse("I ONLY meet on Mondays from 1-3 and Tuesdays 2-4")

        assert result.exclusive is True
        assert len(result.proposals) == 2
        assert result.proposals[0].params == {"day_of_week": 0, "start": "13:00", "end": "15:00"}
        assert result.proposals[1].params == {"day_of_week": 1, "start": "14:00", "end": "16:00"}

    def test_default_is_not_exclusive(self) -> None:
        response = {
            "proposals": [
                {
                    "rule_type": "block_day_of_week",
                    "enforcement": "hard",
                    "day_of_week": 4,
                    "human_summary": "No Fridays.",
                    "confidence": 0.95,
                }
            ],
            "could_not_parse": None,
        }
        result = _fake_service(response).parse("No appointments on Fridays")

        assert result.exclusive is False


class TestRefusalReason:
    """A refusal says which kind it is, so a caller can be specific."""

    @pytest.mark.parametrize(
        "reason",
        ["ambiguous", "out_of_scope", "multi_intent"],
    )
    def test_a_named_refusal_reason_is_carried_through(self, reason: str) -> None:
        service = _fake_service(
            {
                "proposals": [],
                "could_not_parse": "Not an availability rule.",
                "refusal_reason": reason,
            }
        )

        result = service.parse("no new patients on Fridays", reference_date=REFERENCE_DATE)

        assert result.proposals == []
        assert result.refusal_reason == reason
        assert result.could_not_parse == "Not an availability rule."

    def test_an_unrecognised_reason_is_dropped_rather_than_passed_on(self) -> None:
        """The set is closed; a caller branching on it must not meet a new member."""
        service = _fake_service(
            {"proposals": [], "could_not_parse": "No.", "refusal_reason": "vibes"}
        )

        result = service.parse("something", reference_date=REFERENCE_DATE)

        assert result.refusal_reason is None

    def test_a_successful_parse_carries_no_reason(self) -> None:
        service = _fake_service(
            {
                "proposals": [
                    {
                        "rule_type": "max_per_day",
                        "enforcement": "hard",
                        "max": 6,
                        "human_summary": "Six a day.",
                        "confidence": 0.95,
                    }
                ]
            }
        )

        result = service.parse("max 6 a day", reference_date=REFERENCE_DATE)

        assert len(result.proposals) == 1
        assert result.refusal_reason is None


class TestConfidenceFloor:
    """An unsure proposal is dropped, never shown and never repaired."""

    @staticmethod
    def _response(confidence: object) -> dict[str, Any]:
        return {
            "proposals": [
                {
                    "rule_type": "block_day_of_week",
                    "enforcement": "hard",
                    "day_of_week": 4,
                    "human_summary": "No Fridays.",
                    "confidence": confidence,
                }
            ]
        }

    def test_a_proposal_below_the_floor_becomes_a_refusal(self) -> None:
        service = _fake_service(self._response(0.4))

        result = service.parse("maybe no Fridays?", reference_date=REFERENCE_DATE)

        assert result.proposals == []
        assert result.refusal_reason == "ambiguous"
        assert result.could_not_parse

    def test_a_proposal_at_the_floor_is_kept(self) -> None:
        service = _fake_service(self._response(0.8))

        result = service.parse("no Fridays", reference_date=REFERENCE_DATE)

        assert [p.rule_type for p in result.proposals] == ["block_day_of_week"]
        assert result.proposals[0].confidence == 0.8

    @pytest.mark.parametrize("confidence", [None, "high", True])
    def test_an_unusable_confidence_fails_closed(self, confidence: object) -> None:
        """Missing is not the same as certain — it is no answer at all."""
        service = _fake_service(self._response(confidence))

        result = service.parse("no Fridays", reference_date=REFERENCE_DATE)

        assert result.proposals == []

    def test_one_unsure_proposal_refuses_the_whole_sentence(self) -> None:
        """Showing the confident half would hide which rule was withheld, and
        the missing one is what leaves time open."""
        service = _fake_service(
            {
                "proposals": [
                    {
                        "rule_type": "buffer_before",
                        "enforcement": "hard",
                        "minutes": 15,
                        "human_summary": "Buffer before.",
                        "confidence": 0.95,
                    },
                    {
                        "rule_type": "buffer_after",
                        "enforcement": "hard",
                        "minutes": 15,
                        "human_summary": "Buffer after.",
                        "confidence": 0.2,
                    },
                ]
            }
        )

        result = service.parse("15 minutes between clients", reference_date=REFERENCE_DATE)

        assert result.proposals == []

    def test_the_floor_is_configurable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AVAILABILITY_PARSE_CONFIDENCE_FLOOR", "0.3")
        get_settings.cache_clear()
        try:
            service = _fake_service(self._response(0.4))
            result = service.parse("no Fridays", reference_date=REFERENCE_DATE)
        finally:
            get_settings.cache_clear()

        assert [p.rule_type for p in result.proposals] == ["block_day_of_week"]
