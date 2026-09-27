# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The chart export wired over Postgres the way the route wires it.

Shared by the harness tests that build a real archive from real rows: the
per-patient export, and the practice export that builds one per chart. The
session is the caller's, already on the practice schema and armed as the
clinician doing the exporting, so every repository reads under that
clinician's row policies.
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.payments.statement import PracticeBlock
from app.repositories.postgres.appointment import PostgresAppointmentRepository
from app.repositories.postgres.claim_receipts import PostgresClaimReceiptRepository
from app.repositories.postgres.claims import PostgresClaimRepository
from app.repositories.postgres.clinician_profile import PostgresClinicianProfileRepository
from app.repositories.postgres.coverage import (
    PostgresPatientCoverageRepository,
    PostgresPayerRepository,
)
from app.repositories.postgres.diagnostic_assessment import (
    PostgresDiagnosticAssessmentRepository,
)
from app.repositories.postgres.intake_document import PostgresIntakeDocumentRepository
from app.repositories.postgres.intake_packet import PostgresIntakePacketRepository
from app.repositories.postgres.medication import PostgresMedicationRepository
from app.repositories.postgres.note import PostgresNotesRepository
from app.repositories.postgres.outcome_measure import PostgresOutcomeMeasureRepository
from app.repositories.postgres.patient import PostgresPatientRepository
from app.repositories.postgres.patient_document import PostgresPatientDocumentRepository
from app.repositories.postgres.patient_intake_artifact import (
    PostgresPatientIntakeArtifactRepository,
)
from app.repositories.postgres.patient_intake_assignment import (
    PostgresPatientIntakeAssignmentRepository,
)
from app.repositories.postgres.patient_intake_signature import (
    PostgresPatientIntakeSignatureRepository,
)
from app.repositories.postgres.patient_message import PostgresPatientMessageRepository
from app.repositories.postgres.patient_payment import PostgresPatientPaymentRepository
from app.repositories.postgres.session import PostgresTherapySessionRepository
from app.repositories.postgres.user import PostgresUserRepository
from app.routes.patient_intake_export import _FormRenderer
from app.services import ExportService
from app.services.export_archive import practitioner_from
from app.services.export_billing import BillingRecordSource
from app.services.export_clinical import ClinicalRecordSource
from app.services.file_storage import LocalFileStorage
from app.services.patient_documents_service import PatientDocumentsService
from app.services.patient_intake_assignment_service import IntakeAssignmentService
from app.services.patient_intake_export_service import IntakeExportService
from app.services.patient_intake_review_service import IntakeReviewService
from app.services.practice_billing_profile import load_billing_profile, load_billing_tax_id

if TYPE_CHECKING:
    from datetime import tzinfo
    from pathlib import Path

    from app.models.export import Practitioner
    from sqlalchemy.orm import Session


def _text(profile: dict[str, object], key: str) -> str | None:
    return cast("str | None", profile.get(key))


def billing_source_for(session: Session) -> BillingRecordSource:
    """The billing record wired as the route wires it, on the same tenant session."""
    users = PostgresUserRepository(session)

    def practice() -> PracticeBlock:
        profile = load_billing_profile(session)
        return PracticeBlock(
            name=_text(profile, "legal_name"),
            address_line1=_text(profile, "address_line1"),
            address_line2=_text(profile, "address_line2"),
            city=_text(profile, "city"),
            state=_text(profile, "state"),
            postal_code=_text(profile, "postal_code"),
            phone=_text(profile, "phone"),
        )

    def timezone(user_id: str) -> tzinfo:
        return ZoneInfo(users.get_preferences(user_id).timezone)

    return BillingRecordSource(
        payments=PostgresPatientPaymentRepository(session),
        coverage=PostgresPatientCoverageRepository(session),
        payers=PostgresPayerRepository(session),
        claims=PostgresClaimRepository(session),
        receipts=PostgresClaimReceiptRepository(session),
        appointments=PostgresAppointmentRepository(session),
        practice=practice,
        tax_id=lambda: load_billing_tax_id(session),
        license_for=PostgresClinicianProfileRepository(session).get,
        timezone=timezone,
    )


def export_service_for(
    session: Session,
    *,
    practice_name: str,
    storage_root: Path,
    app_url: str = "https://pablo.example",
) -> ExportService:
    """The chart export over this session's repositories.

    The practitioner comes from the practice's billing profile and the
    clinician's profile, notes are labelled by the built-in note types,
    uploaded files come through the document service and a local store at
    ``storage_root``, each submitted form is rendered by the intake export's
    own renderer, and the clinical and billing records through their own
    repositories.
    """
    note_types = NoteTypeRegistry()
    register_builtin_note_types(note_types)
    clinician_profiles = PostgresClinicianProfileRepository(session)

    def practitioner(user_id: str) -> Practitioner:
        return practitioner_from(load_billing_profile(session), clinician_profiles.get(user_id))

    documents = PatientDocumentsService(
        repo=PostgresPatientDocumentRepository(session),
        settings=Mock(patient_documents_gcs_bucket=str(storage_root)),
        storage=LocalFileStorage(),
    )
    assignment_repo = PostgresPatientIntakeAssignmentRepository(session)
    packets = PostgresIntakePacketRepository(session)
    assignments = IntakeAssignmentService(assignment_repo, packets)
    forms = _FormRenderer(
        assignments,
        IntakeExportService(
            assignments,
            IntakeReviewService(assignment_repo, packets),
            PostgresPatientIntakeSignatureRepository(session),
            PostgresIntakeDocumentRepository(session),
            PostgresPatientIntakeArtifactRepository(session),
            PostgresPatientDocumentRepository(session),
        ),
        practice_name,
        UTC,
        app_url,
    )
    clinical = ClinicalRecordSource(
        appointments=PostgresAppointmentRepository(session),
        users=PostgresUserRepository(session),
        outcome_measures=PostgresOutcomeMeasureRepository(session),
        messages=PostgresPatientMessageRepository(session),
        medications=PostgresMedicationRepository(session),
        diagnoses=PostgresDiagnosticAssessmentRepository(session),
    )
    return ExportService(
        PostgresPatientRepository(session),
        PostgresTherapySessionRepository(session),
        PostgresNotesRepository(session),
        practitioner=practitioner,
        note_types=note_types,
        documents=documents,
        intake_forms=forms.submitted_forms,
        clinical_record=clinical.read,
        billing_record=billing_source_for(session).read,
    )


__all__: list[str] = ["billing_source_for", "export_service_for"]
