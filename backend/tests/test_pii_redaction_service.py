# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for PIIRedactionService.

``presidio_analyzer`` (the detection engine) and ``faker`` (used by the
naturalization step) are both referenced only as mypy override targets in
pyproject.toml — neither is declared in any dependency group, Dockerfile, or
CI job, so they are not actually importable in this environment. Both are
stubbed via ``sys.modules`` before the service module is imported, the same
approach ``test_cloud_sql_connector.py`` uses for the optional Cloud SQL
connector library. This lets the tests exercise the service's own detection
-> placeholder -> splice -> naturalize pipeline without needing either
library for real.
"""

from __future__ import annotations

import random
import sys
import types
from dataclasses import dataclass

# --- stub presidio_analyzer -------------------------------------------------


@dataclass
class _FakeRecognizerResult:
    """Duck-typed stand-in for presidio_analyzer.RecognizerResult.

    PIIRedactionService only reads entity_type/start/end off each result.
    """

    entity_type: str
    start: int
    end: int
    score: float = 0.85


class _FakeAnalyzerEngine:
    """Stand-in for presidio's AnalyzerEngine — returns pre-programmed results.

    Each test sets ``.results`` directly instead of relying on real NLP.
    """

    def __init__(self) -> None:
        self.results: list[_FakeRecognizerResult] = []

    def analyze(
        self, *, text: str, entities: list[str], language: str
    ) -> list[_FakeRecognizerResult]:
        return self.results


_fake_presidio_module = types.ModuleType("presidio_analyzer")
_fake_presidio_module.AnalyzerEngine = _FakeAnalyzerEngine  # type: ignore[attr-defined]
sys.modules.setdefault("presidio_analyzer", _fake_presidio_module)


# --- stub faker --------------------------------------------------------------


class _FakeFaker:
    """Deterministic stand-in for faker.Faker — reseedable, no data files."""

    def __init__(self) -> None:
        self._rng = random.Random()  # noqa: S311 — deterministic test fake, not crypto

    def seed_instance(self, seed: int) -> None:
        self._rng = random.Random(seed)  # noqa: S311 — deterministic test fake, not crypto

    def name(self) -> str:
        return f"Placeholder Person {self._rng.randrange(10_000)}"

    def phone_number(self) -> str:
        return f"555-{self._rng.randrange(100, 999)}-{self._rng.randrange(1000, 9999)}"

    def email(self) -> str:
        return f"user{self._rng.randrange(10_000)}@example.test"

    def city(self) -> str:
        return f"Faketown{self._rng.randrange(1000)}"

    def state_abbr(self) -> str:
        return self._rng.choice(["CA", "NY", "TX", "WA", "OR"])

    def date(self) -> str:
        return f"2024-{self._rng.randrange(1, 13):02d}-{self._rng.randrange(1, 29):02d}"

    def ssn(self) -> str:
        area, group, serial = (
            self._rng.randrange(100, 999),
            self._rng.randrange(10, 99),
            self._rng.randrange(1000, 9999),
        )
        return f"{area}-{group}-{serial}"

    def random_number(self, digits: int = 6) -> int:
        return self._rng.randrange(10 ** (digits - 1), 10**digits)

    def license_plate(self) -> str:
        return f"FAKE-{self._rng.randrange(1000)}"


_fake_faker_module = types.ModuleType("faker")
_fake_faker_module.Faker = _FakeFaker  # type: ignore[attr-defined]
sys.modules.setdefault("faker", _fake_faker_module)


from app.services.pii_redaction_service import PIIRedactionService  # noqa: E402

# --- helpers -----------------------------------------------------------------


def _span(text: str, substring: str, occurrence: int = 1) -> tuple[int, int]:
    """Return the (start, end) offsets of the nth (1-indexed) occurrence."""
    idx = -1
    for _ in range(occurrence):
        idx = text.index(substring, idx + 1)
    return idx, idx + len(substring)


# --- tests ---------------------------------------------------------------


class TestPassThrough:
    def test_clean_text_with_no_entities_is_unchanged(self) -> None:
        service = PIIRedactionService()
        service.analyzer.results = []
        text = "The weather was nice today and we discussed coping strategies."

        result = service.redact(text, session_id="session-1")

        assert result.redacted_text == text
        assert result.naturalized_text == text
        assert result.entities == []
        assert result.entity_count == 0


class TestSingleEntityRedaction:
    def test_person_name_is_replaced_with_placeholder(self) -> None:
        text = "Patient Jane Placeholder reported improved mood."
        start, end = _span(text, "Jane Placeholder")
        service = PIIRedactionService()
        service.analyzer.results = [_FakeRecognizerResult("PERSON", start, end)]

        result = service.redact(text, session_id="session-1")

        assert result.redacted_text == "Patient <PERSON_1> reported improved mood."
        assert result.entity_count == 1
        entity = result.entities[0]
        assert entity.entity_type == "PERSON"
        assert entity.original_text == "Jane Placeholder"
        assert entity.placeholder == "<PERSON_1>"
        assert entity.start == start
        assert entity.end == end

    def test_naturalized_text_replaces_placeholder_and_hides_original(self) -> None:
        text = "Patient Jane Placeholder reported improved mood."
        start, end = _span(text, "Jane Placeholder")
        service = PIIRedactionService()
        service.analyzer.results = [_FakeRecognizerResult("PERSON", start, end)]

        result = service.redact(text, session_id="session-1")

        assert "<PERSON_1>" not in result.naturalized_text
        assert "Jane Placeholder" not in result.naturalized_text
        assert result.naturalized_text.startswith("Patient ")
        assert result.naturalized_text.endswith(" reported improved mood.")


class TestMultipleEntityTypes:
    def test_each_entity_type_numbers_independently(self) -> None:
        text = "Contact Jane Placeholder at jane@example.test or 555-000-1111."
        person_span = _span(text, "Jane Placeholder")
        email_span = _span(text, "jane@example.test")
        phone_span = _span(text, "555-000-1111")
        service = PIIRedactionService()
        service.analyzer.results = [
            _FakeRecognizerResult("PERSON", *person_span),
            _FakeRecognizerResult("EMAIL_ADDRESS", *email_span),
            _FakeRecognizerResult("PHONE_NUMBER", *phone_span),
        ]

        result = service.redact(text, session_id="session-1")

        assert result.entity_count == 3
        assert result.redacted_text == (
            "Contact <PERSON_1> at <EMAIL_ADDRESS_1> or <PHONE_NUMBER_1>."
        )
        # Entities preserve original left-to-right order, not detection order.
        assert [e.entity_type for e in result.entities] == [
            "PERSON",
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
        ]


class TestRepeatedEntityDeduplication:
    def test_same_person_mentioned_twice_reuses_placeholder_number(self) -> None:
        text = "Jane Placeholder called. Jane Placeholder wants a callback."
        first_span = _span(text, "Jane Placeholder", occurrence=1)
        second_span = _span(text, "Jane Placeholder", occurrence=2)
        service = PIIRedactionService()
        service.analyzer.results = [
            _FakeRecognizerResult("PERSON", *first_span),
            _FakeRecognizerResult("PERSON", *second_span),
        ]

        result = service.redact(text, session_id="session-1")

        assert result.entity_count == 2
        assert result.redacted_text == "<PERSON_1> called. <PERSON_1> wants a callback."

    def test_case_insensitive_match_reuses_same_placeholder(self) -> None:
        text = "JANE PLACEHOLDER called. jane placeholder wants a callback."
        first_span = _span(text, "JANE PLACEHOLDER")
        second_span = _span(text, "jane placeholder")
        service = PIIRedactionService()
        service.analyzer.results = [
            _FakeRecognizerResult("PERSON", *first_span),
            _FakeRecognizerResult("PERSON", *second_span),
        ]

        result = service.redact(text, session_id="session-1")

        assert result.redacted_text == "<PERSON_1> called. <PERSON_1> wants a callback."

    def test_two_distinct_people_get_sequential_numbers(self) -> None:
        text = "Jane Placeholder referred John Sample."
        jane_span = _span(text, "Jane Placeholder")
        john_span = _span(text, "John Sample")
        service = PIIRedactionService()
        service.analyzer.results = [
            _FakeRecognizerResult("PERSON", *jane_span),
            _FakeRecognizerResult("PERSON", *john_span),
        ]

        result = service.redact(text, session_id="session-1")

        assert result.redacted_text == "<PERSON_1> referred <PERSON_2>."


class TestSplicingCorrectness:
    def test_earlier_entity_offsets_unaffected_by_later_replacement_length(self) -> None:
        # The PERSON placeholder is shorter than "Jane Alexandra Placeholder"
        # and the PHONE_NUMBER placeholder is longer than "555-0100". Splicing
        # back-to-front must not let either replacement shift the other's
        # recorded offsets.
        text = "Jane Alexandra Placeholder called from 555-0100 about billing."
        person_span = _span(text, "Jane Alexandra Placeholder")
        phone_span = _span(text, "555-0100")
        service = PIIRedactionService()
        service.analyzer.results = [
            _FakeRecognizerResult("PERSON", *person_span),
            _FakeRecognizerResult("PHONE_NUMBER", *phone_span),
        ]

        result = service.redact(text, session_id="session-1")

        assert result.redacted_text == "<PERSON_1> called from <PHONE_NUMBER_1> about billing."

    def test_entity_spanning_the_entire_text(self) -> None:
        text = "Jane Placeholder"
        span = _span(text, "Jane Placeholder")
        service = PIIRedactionService()
        service.analyzer.results = [_FakeRecognizerResult("PERSON", *span)]

        result = service.redact(text, session_id="session-1")

        assert result.redacted_text == "<PERSON_1>"


class TestNaturalizationDeterminism:
    def test_same_session_id_and_entities_yield_identical_naturalized_text(self) -> None:
        text = "Jane Placeholder reported improved mood."
        span = _span(text, "Jane Placeholder")

        def run() -> str:
            service = PIIRedactionService()
            service.analyzer.results = [_FakeRecognizerResult("PERSON", *span)]
            return service.redact(text, session_id="same-session").naturalized_text

        assert run() == run()

    def test_different_session_id_yields_different_naturalized_text(self) -> None:
        text = "Jane Placeholder reported improved mood."
        span = _span(text, "Jane Placeholder")

        def run(session_id: str) -> str:
            service = PIIRedactionService()
            service.analyzer.results = [_FakeRecognizerResult("PERSON", *span)]
            return service.redact(text, session_id=session_id).naturalized_text

        # Not a mathematical guarantee for arbitrary seed pairs, but with a
        # 10,000-value name space a collision between two fixed session ids
        # is practically impossible.
        assert run("session-a") != run("session-b")


class TestEntityEnrichment:
    def test_fake_replacement_is_recorded_on_the_entity(self) -> None:
        text = "Jane Placeholder called."
        span = _span(text, "Jane Placeholder")
        service = PIIRedactionService()
        service.analyzer.results = [_FakeRecognizerResult("PERSON", *span)]

        result = service.redact(text, session_id="session-1")

        assert result.entities[0].fake_replacement != ""
        assert result.entities[0].fake_replacement in result.naturalized_text


class TestRedactTranscript:
    def test_redact_transcript_delegates_to_redact(self) -> None:
        text = "Jane Placeholder attended the session."
        span = _span(text, "Jane Placeholder")
        service = PIIRedactionService()
        service.analyzer.results = [_FakeRecognizerResult("PERSON", *span)]

        result = service.redact_transcript(text, session_id="session-1")

        assert result.redacted_text == "<PERSON_1> attended the session."
        assert result.entity_count == 1
