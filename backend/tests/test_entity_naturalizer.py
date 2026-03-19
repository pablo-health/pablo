# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for EntityNaturalizer service."""


from app.services.entity_naturalizer import EntityNaturalizer, RedactedEntity


def test_naturalization_is_deterministic() -> None:
    """Same seed produces same fake names."""
    naturalizer1 = EntityNaturalizer(seed="session-123")
    naturalizer2 = EntityNaturalizer(seed="session-123")

    entity = RedactedEntity(
        entity_type="PERSON",
        start=0,
        end=10,
        original_text="John Smith",
        placeholder="<PERSON_1>",
    )

    result1 = naturalizer1.generate_fake_for_entity(entity)
    result2 = naturalizer2.generate_fake_for_entity(entity)

    assert result1 == result2


def test_different_seeds_produce_different_names() -> None:
    """Different seeds produce different fake names."""
    naturalizer1 = EntityNaturalizer(seed="session-123")
    naturalizer2 = EntityNaturalizer(seed="session-456")

    entity = RedactedEntity(
        entity_type="PERSON",
        start=0,
        end=10,
        original_text="John Smith",
        placeholder="<PERSON_1>",
    )

    result1 = naturalizer1.generate_fake_for_entity(entity)
    result2 = naturalizer2.generate_fake_for_entity(entity)

    # Very unlikely to be the same (not impossible, but extremely rare)
    assert result1 != result2


def test_person_entity_generates_full_name() -> None:
    """PERSON entity generates a full name."""
    naturalizer = EntityNaturalizer(seed="test")

    entity = RedactedEntity(
        entity_type="PERSON",
        start=0,
        end=10,
        original_text="John Smith",
        placeholder="<PERSON_1>",
    )

    result = naturalizer.generate_fake_for_entity(entity)

    # Should have at least first and last name (space in between)
    assert " " in result
    assert len(result) > 3


def test_person_entity_preserves_doctor_title() -> None:
    """PERSON entity with Dr. title preserves the title."""
    naturalizer = EntityNaturalizer(seed="test")

    entity = RedactedEntity(
        entity_type="PERSON",
        start=0,
        end=12,
        original_text="Dr. Martinez",
        placeholder="<PERSON_1>",
    )

    result = naturalizer.generate_fake_for_entity(entity)

    assert result.startswith("Dr. ")


def test_phone_number_entity_generates_valid_format() -> None:
    """PHONE_NUMBER entity generates a phone number."""
    naturalizer = EntityNaturalizer(seed="test")

    entity = RedactedEntity(
        entity_type="PHONE_NUMBER",
        start=0,
        end=12,
        original_text="206-555-1234",
        placeholder="<PHONE_NUMBER_1>",
    )

    result = naturalizer.generate_fake_for_entity(entity)

    # Should contain digits
    assert any(char.isdigit() for char in result)


def test_email_entity_generates_valid_email() -> None:
    """EMAIL_ADDRESS entity generates a valid email."""
    naturalizer = EntityNaturalizer(seed="test")

    entity = RedactedEntity(
        entity_type="EMAIL_ADDRESS",
        start=0,
        end=20,
        original_text="john@example.com",
        placeholder="<EMAIL_ADDRESS_1>",
    )

    result = naturalizer.generate_fake_for_entity(entity)

    assert "@" in result
    assert "." in result


def test_location_entity_generates_city_state() -> None:
    """LOCATION entity generates City, State format."""
    naturalizer = EntityNaturalizer(seed="test")

    entity = RedactedEntity(
        entity_type="LOCATION",
        start=0,
        end=7,
        original_text="Seattle",
        placeholder="<LOCATION_1>",
    )

    result = naturalizer.generate_fake_for_entity(entity)

    # Should have format "City, ST"
    assert ", " in result
    parts = result.split(", ")
    assert len(parts) == 2
    # State abbreviation should be 2 characters
    assert len(parts[1]) == 2


def test_datetime_preserves_relative_timing() -> None:
    """DATE_TIME entity preserves relative timing."""
    naturalizer = EntityNaturalizer(seed="test")

    relative_phrases = [
        "3 weeks ago",
        "last Tuesday",
        "next month",
        "yesterday",
        "tomorrow",
    ]

    for idx, phrase in enumerate(relative_phrases, start=1):
        entity = RedactedEntity(
            entity_type="DATE_TIME",
            start=0,
            end=len(phrase),
            original_text=phrase,
            placeholder=f"<DATE_TIME_{idx}>",  # Unique placeholder for each
        )

        result = naturalizer.generate_fake_for_entity(entity)

        # Should preserve the original relative phrase
        assert result == phrase


def test_datetime_generates_fake_absolute_date() -> None:
    """DATE_TIME entity generates fake date for absolute dates."""
    naturalizer = EntityNaturalizer(seed="test")

    entity = RedactedEntity(
        entity_type="DATE_TIME",
        start=0,
        end=16,
        original_text="January 15, 2026",
        placeholder="<DATE_TIME_1>",
    )

    result = naturalizer.generate_fake_for_entity(entity)

    # Should not be the original date
    assert result != "January 15, 2026"
    # Should be a date-like string
    assert len(result) > 0


def test_ssn_entity_generates_fake_ssn() -> None:
    """US_SSN entity generates a fake SSN."""
    naturalizer = EntityNaturalizer(seed="test")

    entity = RedactedEntity(
        entity_type="US_SSN",
        start=0,
        end=11,
        original_text="123-45-6789",
        placeholder="<US_SSN_1>",
    )

    result = naturalizer.generate_fake_for_entity(entity)

    # Should contain digits and dashes
    assert any(char.isdigit() for char in result)


def test_medical_license_generates_fake_license() -> None:
    """MEDICAL_LICENSE entity generates a fake license number."""
    naturalizer = EntityNaturalizer(seed="test")

    entity = RedactedEntity(
        entity_type="MEDICAL_LICENSE",
        start=0,
        end=10,
        original_text="ML-123456",
        placeholder="<MEDICAL_LICENSE_1>",
    )

    result = naturalizer.generate_fake_for_entity(entity)

    # Should start with ML-
    assert result.startswith("ML-")
    # Should have 6 digits after ML-
    assert len(result) == 9  # ML- (3 chars) + 6 digits


def test_unknown_entity_type_returns_placeholder() -> None:
    """Unknown entity type returns the placeholder as-is."""
    naturalizer = EntityNaturalizer(seed="test")

    entity = RedactedEntity(
        entity_type="UNKNOWN_TYPE",
        start=0,
        end=10,
        original_text="some text",
        placeholder="<UNKNOWN_TYPE_1>",
    )

    result = naturalizer.generate_fake_for_entity(entity)

    # Should return placeholder for unknown types
    assert result == "<UNKNOWN_TYPE_1>"


def test_naturalize_replaces_all_placeholders() -> None:
    """Naturalize method replaces all placeholders in text."""
    naturalizer = EntityNaturalizer(seed="test")

    redacted_text = "<PERSON_1> called from <LOCATION_1> about <PHONE_NUMBER_1>"

    entities = [
        RedactedEntity(
            entity_type="PERSON",
            start=0,
            end=10,
            original_text="John Smith",
            placeholder="<PERSON_1>",
        ),
        RedactedEntity(
            entity_type="LOCATION",
            start=23,
            end=30,
            original_text="Seattle",
            placeholder="<LOCATION_1>",
        ),
        RedactedEntity(
            entity_type="PHONE_NUMBER",
            start=37,
            end=49,
            original_text="206-555-1234",
            placeholder="<PHONE_NUMBER_1>",
        ),
    ]

    result = naturalizer.naturalize(redacted_text, entities)

    # Should not contain placeholders
    assert "<PERSON_1>" not in result
    assert "<LOCATION_1>" not in result
    assert "<PHONE_NUMBER_1>" not in result

    # Should contain natural-looking replacements
    assert "<" not in result
    assert ">" not in result


def test_naturalize_updates_entity_fake_replacement() -> None:
    """Naturalize method updates entity.fake_replacement field."""
    naturalizer = EntityNaturalizer(seed="test")

    redacted_text = "<PERSON_1> discussed anxiety"

    entity = RedactedEntity(
        entity_type="PERSON",
        start=0,
        end=10,
        original_text="John Smith",
        placeholder="<PERSON_1>",
    )

    entities = [entity]

    naturalizer.naturalize(redacted_text, entities)

    # Entity should have fake_replacement populated
    assert entity.fake_replacement != ""
    assert len(entity.fake_replacement) > 0


def test_consistency_same_placeholder_same_replacement() -> None:
    """Same placeholder in text gets same replacement throughout."""
    naturalizer = EntityNaturalizer(seed="test")

    redacted_text = "<PERSON_1> arrived. <PERSON_1> discussed anxiety. Dr. <PERSON_1> said..."

    # Create entity for <PERSON_1>
    entity = RedactedEntity(
        entity_type="PERSON",
        start=0,
        end=10,
        original_text="John Smith",
        placeholder="<PERSON_1>",
    )

    entities = [entity]

    result = naturalizer.naturalize(redacted_text, entities)

    # All occurrences of <PERSON_1> should be replaced with the same fake name
    # Count how many times the fake replacement appears
    fake_name = entity.fake_replacement
    assert result.count(fake_name) == 3
