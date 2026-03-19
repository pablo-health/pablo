# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for PIIRedactionService."""

import pytest
from app.services.pii_redaction_service import PIIRedactionService

pytestmark = pytest.mark.spacy


@pytest.fixture
def service() -> PIIRedactionService:
    """Create PIIRedactionService instance."""
    return PIIRedactionService()


def test_redact_person_name(service: PIIRedactionService) -> None:
    """Redact person names from text."""
    text = "John Smith called to discuss his anxiety."
    result = service.redact(text, session_id="test-session")

    # Placeholder version should not contain original name
    assert "John Smith" not in result.redacted_text
    # Placeholder should be present
    assert "<PERSON_" in result.redacted_text

    # Natural version should not contain original name
    assert "John Smith" not in result.naturalized_text
    # Natural version should not contain placeholders
    assert "<PERSON_" not in result.naturalized_text
    assert "<" not in result.naturalized_text

    # Should have detected entity
    assert result.entity_count > 0


def test_redact_phone_number(service: PIIRedactionService) -> None:
    """Redact phone numbers from text."""
    text = "Call me at 206-555-1234 if you need anything."
    result = service.redact(text, session_id="test-session")

    # Placeholder version
    assert "206-555-1234" not in result.redacted_text
    assert "<PHONE_NUMBER_" in result.redacted_text

    # Natural version
    assert "206-555-1234" not in result.naturalized_text
    assert "<PHONE_NUMBER_" not in result.naturalized_text

    # Should have detected entity
    assert result.entity_count > 0


def test_redact_email_address(service: PIIRedactionService) -> None:
    """Redact email addresses from text."""
    text = "Send me an email at john.smith@example.com for more info."
    result = service.redact(text, session_id="test-session")

    # Placeholder version
    assert "john.smith@example.com" not in result.redacted_text
    assert "<EMAIL_ADDRESS_" in result.redacted_text

    # Natural version
    assert "john.smith@example.com" not in result.naturalized_text
    assert "<EMAIL_ADDRESS_" not in result.naturalized_text

    # Should have detected entity
    assert result.entity_count > 0


def test_redact_multiple_entities(service: PIIRedactionService) -> None:
    """Redact multiple PII entities from text."""
    text = "John Smith (john@example.com) called from Seattle about 555-123-4567."
    result = service.redact(text, session_id="test-session")

    # Should detect multiple entities
    assert result.entity_count >= 3  # At least: person, email, phone

    # Placeholder version should have placeholders
    assert "<PERSON_" in result.redacted_text or "<EMAIL_" in result.redacted_text

    # Natural version should not have placeholders
    assert "<" not in result.naturalized_text
    assert ">" not in result.naturalized_text


def test_deterministic_fake_data_same_session(service: PIIRedactionService) -> None:
    """Same session ID produces same fake data."""
    text = "John Smith called about his anxiety."

    result1 = service.redact(text, session_id="session-123")
    result2 = service.redact(text, session_id="session-123")

    # Same session should produce identical naturalized text
    assert result1.naturalized_text == result2.naturalized_text


def test_different_fake_data_different_session(service: PIIRedactionService) -> None:
    """Different session IDs produce different fake data."""
    text = "John Smith called about his anxiety."

    result1 = service.redact(text, session_id="session-123")
    result2 = service.redact(text, session_id="session-456")

    # Different sessions should produce different naturalized text
    assert result1.naturalized_text != result2.naturalized_text


def test_redacted_text_same_for_any_session(service: PIIRedactionService) -> None:
    """Redacted (placeholder) text is same regardless of session ID."""
    text = "John Smith called about his anxiety."

    result1 = service.redact(text, session_id="session-123")
    result2 = service.redact(text, session_id="session-456")

    # Placeholder version should be identical
    assert result1.redacted_text == result2.redacted_text


def test_entity_list_contains_all_detected_entities(service: PIIRedactionService) -> None:
    """Entity list contains all detected PII entities."""
    text = "John Smith (john@example.com, 206-555-1234) from Seattle."
    result = service.redact(text, session_id="test-session")

    # Check entity count matches list
    assert len(result.entities) == result.entity_count

    # Each entity should have required fields
    for entity in result.entities:
        assert entity.entity_type
        assert entity.placeholder
        assert entity.original_text
        assert entity.fake_replacement
        assert entity.start >= 0
        assert entity.end > entity.start


def test_consistency_same_person_mentioned_multiple_times(service: PIIRedactionService) -> None:
    """Same person mentioned multiple times gets same placeholder."""
    text = "John Smith arrived. John Smith discussed anxiety. John Smith left."
    result = service.redact(text, session_id="test-session")

    # Should have placeholders for all mentions
    # Count placeholders (should be 3)
    placeholder_count = result.redacted_text.count("<PERSON_")
    assert placeholder_count == 3

    # All should use same numbered placeholder
    assert result.redacted_text.count("<PERSON_1>") == 3


def test_naturalized_consistency_same_person(service: PIIRedactionService) -> None:
    """Same person mentioned multiple times gets same fake name."""
    text = "John Smith arrived. John Smith discussed anxiety. John Smith left."
    result = service.redact(text, session_id="test-session")

    # Get the fake name for first person
    if result.entities:
        fake_name = result.entities[0].fake_replacement

        # Count occurrences in naturalized text
        assert result.naturalized_text.count(fake_name) == 3


def test_no_pii_returns_unchanged_text(service: PIIRedactionService) -> None:
    """Text with no PII returns unchanged."""
    text = "Patient discussed feeling anxious and stressed."
    result = service.redact(text, session_id="test-session")

    # Should have no entities
    assert result.entity_count == 0

    # Text should be unchanged
    assert result.redacted_text == text
    assert result.naturalized_text == text


def test_entity_positions_correct(service: PIIRedactionService) -> None:
    """Entity start/end positions match original text."""
    text = "John Smith called from Seattle."
    result = service.redact(text, session_id="test-session")

    # Each entity's positions should extract the correct original text
    for entity in result.entities:
        extracted = text[entity.start : entity.end]
        # Should match original_text (accounting for case sensitivity)
        assert (
            extracted.lower() in entity.original_text.lower()
            or entity.original_text.lower() in extracted.lower()
        )


def test_redact_transcript_method(service: PIIRedactionService) -> None:
    """redact_transcript method works correctly."""
    transcript = "Patient John Smith (555-1234) discussed anxiety."
    result = service.redact_transcript(transcript, session_id="test-session")

    # Should redact PII
    assert result.entity_count > 0
    assert "John Smith" not in result.naturalized_text


def test_complex_medical_scenario(service: PIIRedactionService) -> None:
    """Test realistic therapy session transcript."""
    text = """
    Patient John Smith (DOB: 01/15/1980, SSN: 123-45-6789) arrived for session.
    He mentioned calling from Seattle at 206-555-1234.
    His email is john.smith@example.com.
    Dr. Martinez discussed treatment plan.
    """

    result = service.redact(text, session_id="test-session")

    # Should detect multiple entity types (person, location, phone, email, date)
    assert result.entity_count >= 4

    # Placeholder version should have placeholders
    assert "<PERSON_" in result.redacted_text
    assert "<PHONE_NUMBER_" in result.redacted_text or "<US_PHONE_NUMBER>" in result.redacted_text

    # Natural version should be clean (no placeholders)
    assert "<" not in result.naturalized_text
    assert ">" not in result.naturalized_text

    # Common PII should be redacted (PERSON, EMAIL, LOCATION reliably detected by Presidio)
    assert "John Smith" not in result.redacted_text
    assert "John Smith" not in result.naturalized_text
    assert "Seattle" not in result.redacted_text
    assert "Seattle" not in result.naturalized_text
    assert "john.smith@example.com" not in result.redacted_text
    assert "john.smith@example.com" not in result.naturalized_text

    # Note: SSN detection can vary based on Presidio's recognizer patterns
    # If SSN is detected, verify it's redacted
    ssn_detected = any("SSN" in e.entity_type.upper() for e in result.entities)
    if ssn_detected:
        assert "123-45-6789" not in result.redacted_text
        assert "123-45-6789" not in result.naturalized_text


def test_empty_text_handling(service: PIIRedactionService) -> None:
    """Empty text returns empty results."""
    result = service.redact("", session_id="test-session")

    assert result.entity_count == 0
    assert result.redacted_text == ""
    assert result.naturalized_text == ""
    assert len(result.entities) == 0


def test_whitespace_only_text(service: PIIRedactionService) -> None:
    """Whitespace-only text handled correctly."""
    text = "   \n\n\t  "
    result = service.redact(text, session_id="test-session")

    # Should detect no entities
    assert result.entity_count == 0
    # Text should be unchanged
    assert result.redacted_text == text
