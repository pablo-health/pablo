# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Repository pattern for data access.

Factory functions return the PostgreSQL implementation. Business logic never
imports a concrete repository class directly -- always use these factories.
"""

from ..scheduling_engine.repositories.appointment import AppointmentRepository
from ..scheduling_engine.repositories.availability_rule import AvailabilityRuleRepository
from .allowlist import (
    AllowlistRepository,
    InMemoryAllowlistRepository,
)
from .ehr_prompt import (
    EhrPromptRepository,
    InMemoryEhrPromptRepository,
)
from .ehr_route import (
    EhrRouteRepository,
    InMemoryEhrRouteRepository,
)
from .note import (
    InMemoryNotesRepository,
    NotesRepository,
)
from .patient import (
    InMemoryPatientRepository,
    PatientRepository,
)
from .session import (
    InMemoryTherapySessionRepository,
    TherapySessionRepository,
)
from .user import (
    InMemoryUserRepository,
    UserRepository,
)


def _get_pg_session():  # type: ignore[no-untyped-def]
    """Get the request-scoped PostgreSQL session."""
    from ..db import get_db_session

    return get_db_session()


def get_user_repository() -> UserRepository:
    """Get user repository instance."""
    from .postgres.user import PostgresUserRepository

    return PostgresUserRepository(_get_pg_session())


def get_allowlist_repository() -> AllowlistRepository:
    """Get allowlist repository instance."""
    from .postgres.allowlist import PostgresAllowlistRepository

    return PostgresAllowlistRepository(_get_pg_session())


def get_patient_repository() -> PatientRepository:
    """Get patient repository instance."""
    from .postgres.patient import PostgresPatientRepository

    return PostgresPatientRepository(_get_pg_session())


def get_session_repository() -> TherapySessionRepository:
    """Get therapy session repository instance."""
    from .postgres.session import PostgresTherapySessionRepository

    return PostgresTherapySessionRepository(_get_pg_session())


def get_notes_repository() -> NotesRepository:
    """Get notes repository instance."""
    from .postgres.note import PostgresNotesRepository

    return PostgresNotesRepository(_get_pg_session())


def get_ehr_prompt_repository() -> EhrPromptRepository:
    """Get EHR prompt repository instance."""
    from .postgres.ehr_prompt import PostgresEhrPromptRepository

    return PostgresEhrPromptRepository(_get_pg_session())


def get_ehr_route_repository() -> EhrRouteRepository:
    """Get EHR route repository instance."""
    from .postgres.ehr_route import PostgresEhrRouteRepository

    return PostgresEhrRouteRepository(_get_pg_session())


def get_appointment_repository() -> AppointmentRepository:
    """Get appointment repository instance."""
    from .postgres.appointment import PostgresAppointmentRepository

    return PostgresAppointmentRepository(_get_pg_session())


def get_availability_rule_repository() -> AvailabilityRuleRepository:
    """Get availability rule repository instance."""
    from .postgres.availability_rule import PostgresAvailabilityRuleRepository

    return PostgresAvailabilityRuleRepository(_get_pg_session())


def get_google_calendar_token_repository():  # type: ignore[no-untyped-def]
    """Get Google Calendar token repository instance."""
    from .postgres.google_calendar_token import (
        PostgresGoogleCalendarTokenRepository,
    )

    return PostgresGoogleCalendarTokenRepository(_get_pg_session())


def get_ical_client_mapping_repository():  # type: ignore[no-untyped-def]
    """Get iCal client mapping repository instance."""
    from .postgres.ical_client_mapping import (
        PostgresICalClientMappingRepository,
    )

    return PostgresICalClientMappingRepository(_get_pg_session())


def get_ical_sync_config_repository():  # type: ignore[no-untyped-def]
    """Get iCal sync config repository instance."""
    from .postgres.ical_sync_config import PostgresICalSyncConfigRepository

    return PostgresICalSyncConfigRepository(_get_pg_session())


def get_clinician_profile_repository():  # type: ignore[no-untyped-def]
    """Get clinician profile repository instance (postgres only)."""
    from .postgres.clinician_profile import PostgresClinicianProfileRepository

    return PostgresClinicianProfileRepository(_get_pg_session())


__all__ = [
    "AllowlistRepository",
    "EhrPromptRepository",
    "EhrRouteRepository",
    "InMemoryAllowlistRepository",
    "InMemoryEhrPromptRepository",
    "InMemoryEhrRouteRepository",
    "InMemoryNotesRepository",
    "InMemoryPatientRepository",
    "InMemoryTherapySessionRepository",
    "InMemoryUserRepository",
    "NotesRepository",
    "PatientRepository",
    "TherapySessionRepository",
    "UserRepository",
    "get_allowlist_repository",
    "get_appointment_repository",
    "get_availability_rule_repository",
    "get_clinician_profile_repository",
    "get_ehr_prompt_repository",
    "get_ehr_route_repository",
    "get_google_calendar_token_repository",
    "get_ical_client_mapping_repository",
    "get_ical_sync_config_repository",
    "get_notes_repository",
    "get_patient_repository",
    "get_session_repository",
    "get_user_repository",
]
