# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Calendar matching inputs and repository helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.calendar_providers.practice_import import ImportProposal
    from app.patients.matching import MatchResult, PatientHint
    from app.repositories.postgres.patient import PostgresPatientRepository
    from app.repositories.postgres.patient_source_mapping import (
        PostgresPatientSourceMappingRepository,
    )
    from sqlalchemy.engine import Connection, Engine

from .helpers import (
    _A,
    _B,
    _MAIN,
    _as,
    _directory,
)


def _tenant_session(engine: Engine, schema: str, user_id: str) -> tuple[Connection, Session]:
    conn = _as(engine, schema, user_id)
    return conn, Session(bind=conn)


def _repos(
    session: Session,
) -> tuple[PostgresPatientRepository, PostgresPatientSourceMappingRepository]:
    from app.repositories.postgres.patient import PostgresPatientRepository
    from app.repositories.postgres.patient_source_mapping import (
        PostgresPatientSourceMappingRepository,
    )

    return PostgresPatientRepository(session), PostgresPatientSourceMappingRepository(session)


def _calendar_service() -> MagicMock:
    """A connected calendar whose main calendar is ``_MAIN``."""
    service = MagicMock()
    service.known_main_calendar_id.return_value = _MAIN
    service.main_calendar_id.return_value = _MAIN
    return service


def _users() -> MagicMock:
    from app.models import User

    users = MagicMock()
    users.get.side_effect = lambda uid: (
        User(
            id=uid, email="b@example.com", name="Hopper", title="Dr.", created_at=datetime.now(UTC)
        )
        if uid == _B
        else None
    )
    users.get_preferences.return_value.model_fields_set = set()
    return users


def _proposal(summary: str) -> ImportProposal:
    from app.calendar_providers.practice_import import build_proposal
    from app.calendar_providers.provider import ImportCandidate

    now = datetime.now(UTC)
    first = now - timedelta(days=21)
    return build_proposal(
        [
            ImportCandidate(
                provider_event_id=f"evt-{i}",
                start=first + timedelta(days=7 * i),
                end=first + timedelta(days=7 * i, minutes=50),
                summary=summary,
                attendee_count=0,
                series_id="rec-1",
            )
            for i in range(6)
        ],
        now=now,
        timezone="UTC",
    )


def _live_charts(engine: Engine, schema: str) -> set[str]:
    conn = _as(engine, schema, _A)
    try:
        return set(_directory(conn))
    finally:
        conn.close()


def _series_key() -> str:
    from app.calendar_providers.source_identity import calendar_source_identifier

    return calendar_source_identifier("rec-1", "", 0, "00:00")


def _match(engine: Engine, schema: str, hint: PatientHint) -> MatchResult:
    from app.patients.matching import MatchContext, match_patient

    conn, session = _tenant_session(engine, schema, _A)
    try:
        patients, mappings = _repos(session)
        return match_patient(hint, MatchContext.for_practice(_A, patients, mappings))
    finally:
        session.close()
        conn.close()
