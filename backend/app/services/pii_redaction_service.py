# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PII redaction service using Microsoft Presidio."""

from typing import ClassVar

from presidio_analyzer import AnalyzerEngine

from .entity_naturalizer import (
    EntityNaturalizer,
    RedactedEntity,
    RedactionResult,
)


class PIIRedactionService:
    """Service for detecting and redacting PII using Presidio."""

    # Entity types to detect
    ENTITY_TYPES: ClassVar[list[str]] = [
        "PERSON",
        "PHONE_NUMBER",
        "EMAIL_ADDRESS",
        "LOCATION",
        "DATE_TIME",
        "US_SSN",
        "MEDICAL_LICENSE",
        "US_DRIVER_LICENSE",
    ]

    def __init__(self) -> None:
        """Initialize Presidio analyzer engine."""
        self.analyzer = AnalyzerEngine()

    def redact(self, text: str, session_id: str) -> RedactionResult:
        """Redact PII from text and generate natural fake replacements.

        Args:
            text: Text to redact
            session_id: Session ID for deterministic fake data generation

        Returns:
            RedactionResult with both placeholder and naturalized versions
        """
        # 1. Analyze text for PII entities
        analyzer_results = self.analyzer.analyze(
            text=text,
            entities=self.ENTITY_TYPES,
            language="en",
        )

        # Sort by start position (reverse order for replacement)
        analyzer_results = sorted(analyzer_results, key=lambda x: x.start, reverse=True)

        # 2. Build mapping of unique entities to placeholder numbers
        # Key: (entity_type, original_text_lowercase) -> placeholder_number
        entity_mapping: dict[tuple[str, str], int] = {}
        entity_counts: dict[str, int] = {}

        # First pass: assign placeholder numbers to unique entities
        for result in sorted(analyzer_results, key=lambda x: x.start):
            entity_type = result.entity_type
            original = text[result.start : result.end]
            key = (entity_type, original.lower())

            if key not in entity_mapping:
                # New unique entity - assign next number
                if entity_type not in entity_counts:
                    entity_counts[entity_type] = 0
                entity_counts[entity_type] += 1
                entity_mapping[key] = entity_counts[entity_type]

        # 3. Replace entities from end to start to preserve positions
        entities: list[RedactedEntity] = []
        redacted_text = text

        for result in analyzer_results:
            entity_type = result.entity_type
            original = text[result.start : result.end]
            key = (entity_type, original.lower())

            # Get placeholder number from mapping
            placeholder_num = entity_mapping[key]
            placeholder = f"<{entity_type}_{placeholder_num}>"

            # Create entity record
            entity = RedactedEntity(
                entity_type=entity_type,
                start=result.start,
                end=result.end,
                original_text=original,
                placeholder=placeholder,
            )
            entities.insert(0, entity)  # Insert at beginning to maintain original order

            # Replace in text
            redacted_text = (
                redacted_text[: result.start] + placeholder + redacted_text[result.end :]
            )

        # 4. Naturalize with session-specific seed
        naturalizer = EntityNaturalizer(seed=session_id)
        naturalized_text = naturalizer.naturalize(redacted_text, entities)

        return RedactionResult(
            redacted_text=redacted_text,
            naturalized_text=naturalized_text,
            entities=entities,
            entity_count=len(entities),
        )

    def redact_transcript(self, content: str, session_id: str) -> RedactionResult:
        """Redact PII from a session transcript.

        Args:
            content: Transcript content
            session_id: Session ID for deterministic fake data generation

        Returns:
            RedactionResult with both versions
        """
        return self.redact(content, session_id)
