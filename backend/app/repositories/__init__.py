# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Repository pattern for data access.

Factory functions return the PostgreSQL implementation. Business logic never
imports a concrete repository class directly -- always use these factories.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..diagnostics.definition_provider import DbDefinitionProvider
    from ..scheduling_engine.repositories.appointment import AppointmentRepository
    from ..scheduling_engine.repositories.availability_rule import AvailabilityRuleRepository
    from .diagnostic_assessment import DiagnosticAssessmentRepository
    from .google_calendar_token import GoogleCalendarTokenRepository
    from .ical_client_mapping import ICalClientMappingRepository
    from .ical_sync_config import ICalSyncConfigRepository
    from .outcome_measure import OutcomeMeasureRepository
    from .postgres.compliance_document import PostgresComplianceDocumentRepository
    from .postgres.compliance_item import PostgresComplianceItemRepository
    from .postgres.supervision import PostgresSupervisionRepository

from .allowlist import (
    AllowlistRepository,
    InMemoryAllowlistRepository,
)
from .chat import (
    ChatRepository,
    InMemoryChatRepository,
)
from .clinician_profile import (
    ClinicianProfile,
    ClinicianProfileRepository,
    InMemoryClinicianProfileRepository,
)
from .ehr_prompt import (
    EhrPromptRepository,
    InMemoryEhrPromptRepository,
)
from .ehr_route import (
    EhrRouteRepository,
    InMemoryEhrRouteRepository,
)
from .identity import (
    IdentityRepository,
    InMemoryIdentityRepository,
)
from .llm_usage import (
    InMemoryLlmUsageRepository,
    LlmUsageRepository,
)
from .medication import (
    InMemoryMedicationRepository,
    MedicationRepository,
)
from .note import (
    InMemoryNotesRepository,
    NotesRepository,
)
from .patient import (
    InMemoryPatientRepository,
    PatientRepository,
)
from .patient_document import (
    InMemoryPatientDocumentRepository,
    PatientDocumentRepository,
)
from .session import (
    InMemoryTherapySessionRepository,
    TherapySessionRepository,
)
from .user import (
    InMemoryUserRepository,
    UserRepository,
)


def _get_pg_session() -> Session:
    """Get the request-scoped PostgreSQL session."""
    from ..db import get_db_session

    return get_db_session()


def get_user_repository() -> UserRepository:
    """Get user repository instance."""
    from .postgres.user import PostgresUserRepository

    return PostgresUserRepository(_get_pg_session())


def get_identity_repository() -> IdentityRepository:
    """Get identity repository instance."""
    from .postgres.identity import PostgresIdentityRepository

    return PostgresIdentityRepository(_get_pg_session())


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


def get_patient_document_repository() -> PatientDocumentRepository:
    """Get patient-document repository instance."""
    from .postgres.patient_document import PostgresPatientDocumentRepository

    return PostgresPatientDocumentRepository(_get_pg_session())


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


def get_google_calendar_token_repository() -> GoogleCalendarTokenRepository:
    """Get Google Calendar token repository instance."""
    from .postgres.google_calendar_token import (
        PostgresGoogleCalendarTokenRepository,
    )

    return PostgresGoogleCalendarTokenRepository(_get_pg_session())


def get_ical_client_mapping_repository() -> ICalClientMappingRepository:
    """Get iCal client mapping repository instance."""
    from .postgres.ical_client_mapping import (
        PostgresICalClientMappingRepository,
    )

    return PostgresICalClientMappingRepository(_get_pg_session())


def get_ical_sync_config_repository() -> ICalSyncConfigRepository:
    """Get iCal sync config repository instance."""
    from .postgres.ical_sync_config import PostgresICalSyncConfigRepository

    return PostgresICalSyncConfigRepository(_get_pg_session())


def get_clinician_profile_repository() -> ClinicianProfileRepository:
    """Get clinician profile repository instance (postgres only)."""
    from .postgres.clinician_profile import PostgresClinicianProfileRepository

    return PostgresClinicianProfileRepository(_get_pg_session())


def get_compliance_item_repository() -> PostgresComplianceItemRepository:
    """Get compliance-item repository instance (postgres only)."""
    from .postgres.compliance_item import PostgresComplianceItemRepository

    return PostgresComplianceItemRepository(_get_pg_session())


def get_compliance_document_repository() -> PostgresComplianceDocumentRepository:
    """Get compliance-document repository instance (postgres only)."""
    from .postgres.compliance_document import PostgresComplianceDocumentRepository

    return PostgresComplianceDocumentRepository(_get_pg_session())


def get_chat_repository() -> ChatRepository:
    """Get chat repository instance."""
    from .postgres.chat import PostgresChatRepository

    return PostgresChatRepository(_get_pg_session())


def get_llm_usage_repository() -> LlmUsageRepository:
    """Get LLM usage repository instance."""
    from .postgres.llm_usage import PostgresLlmUsageRepository

    return PostgresLlmUsageRepository(_get_pg_session())


def get_outcome_measure_repository() -> OutcomeMeasureRepository:
    """Get outcome measure repository instance."""
    from .postgres.outcome_measure import PostgresOutcomeMeasureRepository

    return PostgresOutcomeMeasureRepository(_get_pg_session())


def get_diagnostic_assessment_repository() -> DiagnosticAssessmentRepository:
    """Get diagnostic assessment repository instance."""
    from .postgres.diagnostic_assessment import PostgresDiagnosticAssessmentRepository

    return PostgresDiagnosticAssessmentRepository(_get_pg_session())


def get_diagnostic_definition_provider() -> DbDefinitionProvider:
    """Get a DB-backed diagnostic definition provider (platform schema)."""
    from ..diagnostics.definition_provider import DbDefinitionProvider

    return DbDefinitionProvider(_get_pg_session())


def get_medication_repository() -> MedicationRepository:
    from .postgres.medication import PostgresMedicationRepository

    return PostgresMedicationRepository(_get_pg_session())


def get_supervision_repository() -> PostgresSupervisionRepository:
    """Get supervision-relationship repository instance (postgres only)."""
    from .postgres.supervision import PostgresSupervisionRepository

    return PostgresSupervisionRepository(_get_pg_session())


__all__ = [
    "AllowlistRepository",
    "ChatRepository",
    "ClinicianProfile",
    "ClinicianProfileRepository",
    "EhrPromptRepository",
    "EhrRouteRepository",
    "IdentityRepository",
    "InMemoryAllowlistRepository",
    "InMemoryChatRepository",
    "InMemoryClinicianProfileRepository",
    "InMemoryEhrPromptRepository",
    "InMemoryEhrRouteRepository",
    "InMemoryIdentityRepository",
    "InMemoryLlmUsageRepository",
    "InMemoryMedicationRepository",
    "InMemoryNotesRepository",
    "InMemoryPatientDocumentRepository",
    "InMemoryPatientRepository",
    "InMemoryTherapySessionRepository",
    "InMemoryUserRepository",
    "LlmUsageRepository",
    "MedicationRepository",
    "NotesRepository",
    "PatientDocumentRepository",
    "PatientRepository",
    "TherapySessionRepository",
    "UserRepository",
    "get_allowlist_repository",
    "get_appointment_repository",
    "get_availability_rule_repository",
    "get_chat_repository",
    "get_clinician_profile_repository",
    "get_compliance_document_repository",
    "get_compliance_item_repository",
    "get_diagnostic_assessment_repository",
    "get_diagnostic_definition_provider",
    "get_ehr_prompt_repository",
    "get_ehr_route_repository",
    "get_google_calendar_token_repository",
    "get_ical_client_mapping_repository",
    "get_ical_sync_config_repository",
    "get_identity_repository",
    "get_llm_usage_repository",
    "get_medication_repository",
    "get_notes_repository",
    "get_outcome_measure_repository",
    "get_patient_document_repository",
    "get_patient_repository",
    "get_session_repository",
    "get_supervision_repository",
    "get_user_repository",
]
