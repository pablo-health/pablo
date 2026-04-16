# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Repository pattern for data access.

Factory functions check settings.database_backend and return the appropriate
implementation (Firestore or PostgreSQL). Business logic never imports a
concrete repository class directly — always use these factories.
"""

from ..scheduling_engine.repositories.appointment import AppointmentRepository
from ..scheduling_engine.repositories.availability_rule import AvailabilityRuleRepository
from ..settings import get_settings
from .allowlist import (
    AllowlistRepository,
    FirestoreAllowlistRepository,
    InMemoryAllowlistRepository,
)
from .appointment import FirestoreAppointmentRepository
from .availability_rule import FirestoreAvailabilityRuleRepository
from .ehr_prompt import (
    EhrPromptRepository,
    FirestoreEhrPromptRepository,
    InMemoryEhrPromptRepository,
)
from .ehr_route import (
    EhrRouteRepository,
    FirestoreEhrRouteRepository,
    InMemoryEhrRouteRepository,
)
from .patient import (
    FirestorePatientRepository,
    InMemoryPatientRepository,
    PatientRepository,
)
from .session import (
    FirestoreTherapySessionRepository,
    InMemoryTherapySessionRepository,
    TherapySessionRepository,
)
from .user import (
    FirestoreUserRepository,
    InMemoryUserRepository,
    UserRepository,
)


def _is_postgres() -> bool:
    return get_settings().database_backend == "postgres"


def _get_pg_session():  # type: ignore[no-untyped-def]
    """Get the request-scoped PostgreSQL session."""
    from ..db import get_db_session

    return get_db_session()


def _get_firestore_db(firestore_db: str | None = None):  # type: ignore[no-untyped-def]
    """Get a Firestore client, optionally for a specific tenant database."""
    if firestore_db:
        from ..database import get_tenant_firestore_client

        return get_tenant_firestore_client(firestore_db)
    from ..database import get_firestore_client

    return get_firestore_client()


def get_user_repository(firestore_db: str | None = None) -> UserRepository:
    """Get user repository instance."""
    if _is_postgres():
        from .postgres.user import PostgresUserRepository

        return PostgresUserRepository(_get_pg_session())
    return FirestoreUserRepository(_get_firestore_db(firestore_db))


def get_allowlist_repository(firestore_db: str | None = None) -> AllowlistRepository:
    """Get allowlist repository instance."""
    if _is_postgres():
        from .postgres.allowlist import PostgresAllowlistRepository

        return PostgresAllowlistRepository(_get_pg_session())
    return FirestoreAllowlistRepository(_get_firestore_db(firestore_db))


def get_patient_repository(firestore_db: str | None = None) -> PatientRepository:
    """Get patient repository instance."""
    if _is_postgres():
        from .postgres.patient import PostgresPatientRepository

        return PostgresPatientRepository(_get_pg_session())
    return FirestorePatientRepository(_get_firestore_db(firestore_db))


def get_session_repository(firestore_db: str | None = None) -> TherapySessionRepository:
    """Get therapy session repository instance."""
    if _is_postgres():
        from .postgres.session import PostgresTherapySessionRepository

        return PostgresTherapySessionRepository(_get_pg_session())
    return FirestoreTherapySessionRepository(_get_firestore_db(firestore_db))


def get_ehr_prompt_repository(firestore_db: str | None = None) -> EhrPromptRepository:
    """Get EHR prompt repository instance."""
    if _is_postgres():
        from .postgres.ehr_prompt import PostgresEhrPromptRepository

        return PostgresEhrPromptRepository(_get_pg_session())
    return FirestoreEhrPromptRepository(_get_firestore_db(firestore_db))


def get_ehr_route_repository(firestore_db: str | None = None) -> EhrRouteRepository:
    """Get EHR route repository instance."""
    if _is_postgres():
        from .postgres.ehr_route import PostgresEhrRouteRepository

        return PostgresEhrRouteRepository(_get_pg_session())
    return FirestoreEhrRouteRepository(_get_firestore_db(firestore_db))


def get_appointment_repository(
    firestore_db: str | None = None,
) -> AppointmentRepository:
    """Get appointment repository instance."""
    if _is_postgres():
        from .postgres.appointment import PostgresAppointmentRepository

        return PostgresAppointmentRepository(_get_pg_session())
    return FirestoreAppointmentRepository(_get_firestore_db(firestore_db))


def get_availability_rule_repository(
    firestore_db: str | None = None,
) -> AvailabilityRuleRepository:
    """Get availability rule repository instance."""
    if _is_postgres():
        from .postgres.availability_rule import PostgresAvailabilityRuleRepository

        return PostgresAvailabilityRuleRepository(_get_pg_session())
    return FirestoreAvailabilityRuleRepository(_get_firestore_db(firestore_db))


def get_google_calendar_token_repository(firestore_db: str | None = None):  # type: ignore[no-untyped-def]
    """Get Google Calendar token repository instance."""
    if _is_postgres():
        from .postgres.google_calendar_token import (
            PostgresGoogleCalendarTokenRepository,
        )

        return PostgresGoogleCalendarTokenRepository(_get_pg_session())
    from .google_calendar_token import GoogleCalendarTokenRepository

    return GoogleCalendarTokenRepository(_get_firestore_db(firestore_db))


def get_ical_client_mapping_repository(firestore_db: str | None = None):  # type: ignore[no-untyped-def]
    """Get iCal client mapping repository instance."""
    if _is_postgres():
        from .postgres.ical_client_mapping import (
            PostgresICalClientMappingRepository,
        )

        return PostgresICalClientMappingRepository(_get_pg_session())
    from .ical_client_mapping import ICalClientMappingRepository

    return ICalClientMappingRepository(_get_firestore_db(firestore_db))


def get_ical_sync_config_repository(firestore_db: str | None = None):  # type: ignore[no-untyped-def]
    """Get iCal sync config repository instance."""
    if _is_postgres():
        from .postgres.ical_sync_config import PostgresICalSyncConfigRepository

        return PostgresICalSyncConfigRepository(_get_pg_session())
    from .ical_sync_config import ICalSyncConfigRepository

    return ICalSyncConfigRepository(_get_firestore_db(firestore_db))


def get_clinician_profile_repository():  # type: ignore[no-untyped-def]
    """Get clinician profile repository instance (postgres only)."""
    from .postgres.clinician_profile import PostgresClinicianProfileRepository

    return PostgresClinicianProfileRepository(_get_pg_session())


__all__ = [
    "AllowlistRepository",
    "EhrPromptRepository",
    "EhrRouteRepository",
    "FirestoreAllowlistRepository",
    "FirestoreAppointmentRepository",
    "FirestoreAvailabilityRuleRepository",
    "FirestoreEhrPromptRepository",
    "FirestoreEhrRouteRepository",
    "FirestorePatientRepository",
    "FirestoreTherapySessionRepository",
    "FirestoreUserRepository",
    "InMemoryAllowlistRepository",
    "InMemoryEhrPromptRepository",
    "InMemoryEhrRouteRepository",
    "InMemoryPatientRepository",
    "InMemoryTherapySessionRepository",
    "InMemoryUserRepository",
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
    "get_patient_repository",
    "get_session_repository",
    "get_user_repository",
]
