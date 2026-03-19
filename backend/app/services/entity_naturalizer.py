# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Entity naturalization service for replacing PII placeholders with realistic fake data."""

import hashlib
from dataclasses import dataclass

from faker import Faker


@dataclass
class RedactedEntity:
    """A detected PII entity that was redacted."""

    entity_type: str  # PERSON, PHONE_NUMBER, etc.
    start: int  # Original start position
    end: int  # Original end position
    original_text: str  # "John Smith" (encrypted at rest)
    placeholder: str  # "<PERSON_1>"
    fake_replacement: str = ""  # "David Chen" - populated during naturalization


@dataclass
class RedactionResult:
    """Result of PII redaction with both placeholder and naturalized versions."""

    redacted_text: str  # Version with placeholders
    naturalized_text: str  # Version with fake names (for export)
    entities: list[RedactedEntity]
    entity_count: int


class EntityNaturalizer:
    """Replace placeholders with realistic fake data."""

    def __init__(self, seed: str | None = None) -> None:
        """Initialize with deterministic seed for reproducibility.

        Args:
            seed: Optional seed for deterministic fake data generation.
                  Same seed produces same fake names.
        """
        self.faker = Faker()
        if seed:
            seed_int = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16)
            self.faker.seed_instance(seed_int)
        # Cache to ensure same placeholder gets same fake value
        self._cache: dict[str, str] = {}

    def _generate_person_name(self, entity: RedactedEntity) -> str:
        """Generate fake person name, preserving titles."""
        if entity.original_text.startswith("Dr. "):
            return f"Dr. {self.faker.last_name()}"
        return str(self.faker.name())

    def _generate_datetime(self, entity: RedactedEntity) -> str:
        """Generate fake datetime, preserving relative timing."""
        original = entity.original_text.lower()
        relative_terms = ["ago", "last", "next", "yesterday", "tomorrow", "week", "month", "year"]
        if any(word in original for word in relative_terms):
            return entity.original_text  # Keep relative timing
        return str(self.faker.date())

    def generate_fake_for_entity(self, entity: RedactedEntity) -> str:
        """Generate appropriate fake data based on entity type.

        Uses caching to ensure same placeholder always gets same fake value.

        Args:
            entity: The redacted entity to generate fake data for

        Returns:
            Realistic fake data matching the entity type
        """
        # Check cache first
        if entity.placeholder in self._cache:
            return self._cache[entity.placeholder]

        generators = {
            "PERSON": self._generate_person_name,
            "PHONE_NUMBER": lambda _: self.faker.phone_number(),
            "EMAIL_ADDRESS": lambda _: self.faker.email(),
            "LOCATION": lambda _: f"{self.faker.city()}, {self.faker.state_abbr()}",
            "DATE_TIME": self._generate_datetime,
            "US_SSN": lambda _: self.faker.ssn(),
            "MEDICAL_LICENSE": lambda _: f"ML-{self.faker.random_number(digits=6)}",
            "US_DRIVER_LICENSE": lambda _: self.faker.license_plate(),
        }

        generator = generators.get(entity.entity_type)
        # Fallback: return placeholder as-is for unknown types
        fake_value = str(generator(entity)) if generator else entity.placeholder  # type: ignore[no-untyped-call]

        # Cache the generated value
        self._cache[entity.placeholder] = fake_value
        return fake_value

    def naturalize(self, redacted_text: str, entities: list[RedactedEntity]) -> str:
        """Replace all placeholders with appropriate fake data.

        Args:
            redacted_text: Text with placeholders like <PERSON_1>
            entities: List of redacted entities to replace

        Returns:
            Text with all placeholders replaced with realistic fake data
        """
        naturalized = redacted_text

        for entity in entities:
            fake_value = self.generate_fake_for_entity(entity)
            entity.fake_replacement = fake_value
            naturalized = naturalized.replace(entity.placeholder, fake_value)

        return naturalized
