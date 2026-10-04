# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The end-to-end stack's stand-in for the availability-parsing model.

``availability_parse_base_url`` sends availability parses to an HTTP service
instead of a model; the stack points it at ``scripts/fake_llm.py``. These run
the stand-in's own app behind the real gateway and the real parse service,
so a reply it gives that the parse schema would refuse fails here rather
than as a grid fallback in a browser spec.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import TYPE_CHECKING, Any

import pytest
from app.routes.scheduling import get_availability_rule_parse_service
from app.services import http_structured_llm_gateway
from app.services.availability_parse_service import AvailabilityRuleParseService
from app.services.hedged_structured_llm_gateway import HedgedStructuredLLMGateway
from app.services.http_structured_llm_gateway import HttpStructuredLLMGateway
from app.settings import Settings, get_settings
from fastapi.testclient import TestClient

from scripts import fake_llm
from scripts.fake_llm import app as fake_llm_app

if TYPE_CHECKING:
    import httpx

BASE_URL = "http://fake-llm:8083"


@pytest.fixture
def stand_in(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Route the gateway's POSTs to the stand-in's app; return what it was sent."""
    client = TestClient(fake_llm_app)
    sent: list[dict[str, Any]] = []

    def post(url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response:
        assert url == f"{BASE_URL}/v1/structured"
        sent.append(json)
        return client.post("/v1/structured", json=json)

    monkeypatch.setattr(http_structured_llm_gateway.httpx, "post", post)
    return sent


def test_a_known_sentence_comes_back_as_the_rules_it_names(
    stand_in: list[dict[str, Any]],
) -> None:
    service = AvailabilityRuleParseService(llm_gateway=HttpStructuredLLMGateway(BASE_URL))

    result = service.parse("9 to 5 Monday to Thursday.")

    assert result.could_not_parse is None
    assert [(p.rule_type, p.params) for p in result.proposals] == [
        ("working_hours", {"day_of_week": day, "start": "09:00", "end": "17:00"})
        for day in range(4)
    ]
    assert all(p.appointment_type_id is None for p in result.proposals)
    # The prompts the model would have had go to the stand-in unchanged.
    assert stand_in[0]["user_prompt"] == "9 to 5 Monday to Thursday."
    assert stand_in[0]["system_prompt"]


def test_an_unknown_sentence_is_a_question_back_not_an_error(
    stand_in: list[dict[str, Any]],
) -> None:
    service = AvailabilityRuleParseService(llm_gateway=HttpStructuredLLMGateway(BASE_URL))

    result = service.parse("whenever, really")

    assert result.proposals == []
    assert result.could_not_parse


def _configure(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    configured = Settings(
        database_url="postgresql://x:x@localhost:5432/x",
        environment="development",
        availability_parse_base_url=BASE_URL,
        **overrides,
    )
    monkeypatch.setattr("app.routes.scheduling.get_settings", lambda: configured)
    monkeypatch.setattr(
        "app.services.hedged_structured_llm_gateway.get_settings", lambda: configured
    )


def test_the_route_uses_the_stand_in_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    gateway = get_availability_rule_parse_service()._llm_gateway
    # Through the same routing policy a model is served by.
    assert isinstance(gateway, HedgedStructuredLLMGateway)
    assert isinstance(gateway._resolve("any-model"), HttpStructuredLLMGateway)

    monkeypatch.setattr("app.routes.scheduling.get_settings", get_settings)
    gateway = get_availability_rule_parse_service()._llm_gateway
    assert isinstance(gateway, HedgedStructuredLLMGateway)
    assert not isinstance(gateway._resolve("any-model"), HttpStructuredLLMGateway)


@pytest.fixture
def fresh_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fake_llm, "_calls", Counter())


@pytest.mark.usefixtures("fresh_counts")
def test_a_first_call_that_fails_is_handed_over_at_once(
    stand_in: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    service = get_availability_rule_parse_service()

    started = time.monotonic()
    result = service.parse("10 to 6 Monday to Thursday")

    assert time.monotonic() - started < 1.0
    assert len(stand_in) == 2
    assert [(p.params["start"], p.params["end"]) for p in result.proposals] == [
        ("10:00", "18:00")
    ] * 4


@pytest.mark.usefixtures("fresh_counts")
def test_a_first_call_that_stalls_gets_company_at_the_threshold(
    stand_in: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fake_llm, "STALL_SECONDS", 0.6)
    _configure(monkeypatch, ai_hedge_after_seconds=0.1)
    service = get_availability_rule_parse_service()

    started = time.monotonic()
    result = service.parse("8 to 4 Monday to Thursday")

    assert time.monotonic() - started < 0.5
    assert len(stand_in) == 2
    assert [(p.params["start"], p.params["end"]) for p in result.proposals] == [
        ("08:00", "16:00")
    ] * 4
