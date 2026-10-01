# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Remembered answers to "which patient does this outside identifier mean?"

A source (an EHR calendar feed, a calendar import) names a client its own
way: initials like "J.A.", a code like "SH00001", a calendar series id. Once
a clinician has said which patient that is — or that it is not a client at
all, like a standing staff meeting — the answer is kept here so the next
record from the same source is settled without asking again. Read and
written through ``app.patients.matching``.

An answer belongs to the practice, not to the clinician who gave it: the key
is ``(scope, source, identifier digest)``, where the scope is ``practice``
for a feed's client codes and one calendar for a calendar's series (see
``app.patients.identifiers``). The identifier is kept only as a keyed
digest. Who answered is recorded beside it, as a fact about the answer.

Rows from before answers were the practice's are keyed by the clinician and
hold the identifier in plain text. They are visible only to that clinician,
and are adopted into the practice's answers — digested and re-scoped — the
first time that clinician's matching reads the source
(:meth:`PatientSourceMappingRepository.adopt_legacy`).
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..patients.identifiers import identifier_digest, is_calendar_scope
from ..utcnow import utc_now

if TYPE_CHECKING:
    from datetime import datetime


#: The identifier is a client, and ``patient_id`` says which.
ANSWER_CLIENT = "client"
#: The identifier is not a client; there is no patient.
ANSWER_NOT_A_CLIENT = "not_a_client"


@dataclass
class PatientSourceMapping:
    """One source identifier, and what it means."""

    scope: str
    """Whose answer this is: ``practice``, or one calendar's."""
    source: str
    identifier_digest: str
    """The keyed digest of the identifier; see ``identifiers.identifier_digest``."""
    patient_id: str | None
    answered_by_user_id: str
    """Who answered. A fact about the answer, not part of its key."""
    created_at: datetime | None = None
    answer: str = ANSWER_CLIENT
    answered_title: str | None = None
    """A keyed digest of the title the answer was given under, when the
    identifier can outlive the client it named (a provider's series id).
    See ``app.calendar_providers.source_identity.answered_title_digest``."""
    session_clinician_user_id: str | None = None
    """Whose session a calendar's answer books for. On a personal calendar
    that is the answerer; unset for a feed's answer."""

    @property
    def doc_id(self) -> str:
        return f"{self.scope}|{self.source}|{self.identifier_digest}"


@dataclass
class LegacyAnswer:
    """An answer from before answers were the practice's: one clinician's, in plain text."""

    user_id: str
    source: str
    source_identifier: str
    patient_id: str | None
    answer: str = ANSWER_CLIENT
    answered_title: str | None = None
    created_at: datetime | None = None


class PatientSourceMappingRepository(ABC):
    """Storage for remembered source identifiers, per practice."""

    @abstractmethod
    def list_by_source(self, scope: str, source: str) -> list[PatientSourceMapping]:
        pass

    @abstractmethod
    def save(self, mapping: PatientSourceMapping) -> None:
        """Insert, or replace the answer with the same scope, source and digest."""

    @abstractmethod
    def adopt_legacy(self, user_id: str, source: str, scope: str) -> int:
        """Move this clinician's pre-practice answers for a source into ``scope``.

        Each is digested and re-keyed; where the practice already holds an
        answer under that key, the newer of the two stands. The old row goes
        either way. Idempotent, and safe for two requests to run at once.
        Returns how many rows were moved.
        """


class InMemoryPatientSourceMappingRepository(PatientSourceMappingRepository):
    """In-memory implementation for tests.

    Mappings are copied on the way in and on the way out, as a database
    would hand back a fresh row for every read, so a change made to one and
    never saved is not seen by the next read.
    """

    def __init__(self) -> None:
        self._mappings: dict[str, PatientSourceMapping] = {}
        self._legacy: list[LegacyAnswer] = []

    def list_by_source(self, scope: str, source: str) -> list[PatientSourceMapping]:
        return [
            copy.deepcopy(m)
            for m in self._mappings.values()
            if (m.scope, m.source) == (scope, source)
        ]

    def save(self, mapping: PatientSourceMapping) -> None:
        if mapping.created_at is None:
            mapping.created_at = utc_now()
        self._mappings[mapping.doc_id] = copy.deepcopy(mapping)

    def remember_legacy(self, answer: LegacyAnswer) -> None:
        """Test helper: a row as the table held it before answers were the practice's."""
        self._legacy.append(copy.deepcopy(answer))

    def legacy_answers(self) -> list[LegacyAnswer]:
        return copy.deepcopy(self._legacy)

    def adopt_legacy(self, user_id: str, source: str, scope: str) -> int:
        moved = 0
        for old in [a for a in self._legacy if (a.user_id, a.source) == (user_id, source)]:
            adopted = PatientSourceMapping(
                scope=scope,
                source=source,
                identifier_digest=identifier_digest(old.source_identifier),
                patient_id=old.patient_id,
                answered_by_user_id=old.user_id,
                created_at=old.created_at,
                answer=old.answer,
                answered_title=old.answered_title,
                session_clinician_user_id=old.user_id if is_calendar_scope(scope) else None,
            )
            current = self._mappings.get(adopted.doc_id)
            if current is None or _newer(adopted, current):
                self._mappings[adopted.doc_id] = adopted
            self._legacy.remove(old)
            moved += 1
        return moved


def _newer(candidate: PatientSourceMapping, current: PatientSourceMapping) -> bool:
    """Whether the candidate answer is the more recent. An undated answer is the oldest."""
    if candidate.created_at is None:
        return False
    if current.created_at is None:
        return True
    return candidate.created_at > current.created_at
