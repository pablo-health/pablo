# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Dashboard summary route.

One aggregate read for the clinician home screen, replacing the panel-by-panel
fan-out (today + week appointments, the full session list, and a blind patient
page) that issued ~6 concurrent requests per load. Folding them into a single
handler keeps one DB connection per dashboard load instead of competing for the
pool, and lets the counts be computed over the *full* set server-side rather
than from a 20-row page the panels happened to fetch.

Audit posture: appointment data is scheduling metadata (consistent with
``GET /api/appointments`` / ``GET /api/sessions/today`` being PHI-exempt), and
last-visit dates are joined for the patients already disclosed by today's
appointments — so no blind patient-list read and none of its spurious
``patient_viewed`` rows. The awaiting-review rows are an actual session
disclosure, so each one shown is audited ``session_viewed``.
"""

from datetime import datetime, tzinfo

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from ..auth.service import require_baa_acceptance
from ..models import AuditAction, User
from ..models.enums import SessionStatus
from ..models.scheduling import AppointmentResponse
from ..repositories import NotesRepository, PatientRepository, TherapySessionRepository
from ..scheduling_engine.services.scheduling import SchedulingService
from ..services import AuditService, get_audit_service
from .scheduling import _to_response, get_owner_timezone, get_scheduling_service
from .sessions import (
    get_notes_repository,
    get_patient_repository,
    get_session_repository,
)

router = APIRouter()

# Rows shown inline on the dashboard; the rest live on the Review worklist.
AWAITING_REVIEW_LIMIT = 5


class AwaitingReviewItem(BaseModel):
    """A session whose note has finished generating and awaits review."""

    session_id: str
    patient_name: str
    session_date: datetime
    status: str
    note_finalized_at: datetime | None = None


class DashboardSummaryResponse(BaseModel):
    """Everything the dashboard panels need in one payload."""

    today_appointments: list[AppointmentResponse]
    # patient_id -> last session date, for the today-appointment patients only.
    last_visit_by_patient: dict[str, datetime | None]
    week_confirmed_count: int
    notes_pending_count: int
    transcription_pending_count: int
    awaiting_review_total: int
    awaiting_review: list[AwaitingReviewItem]


@router.get("/api/dashboard/summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(
    request: Request,
    today_start: str = Query(..., description="Today range start (ISO 8601)"),
    today_end: str = Query(..., description="Today range end (ISO 8601)"),
    week_start: str = Query(..., description="Rest-of-week range start (ISO 8601)"),
    week_end: str = Query(..., description="Rest-of-week range end (ISO 8601)"),
    user: User = Depends(require_baa_acceptance),
    scheduling: SchedulingService = Depends(get_scheduling_service),
    session_repo: TherapySessionRepository = Depends(get_session_repository),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    notes_repo: NotesRepository = Depends(get_notes_repository),
    audit: AuditService = Depends(get_audit_service),
    tz: tzinfo = Depends(get_owner_timezone),
) -> DashboardSummaryResponse:
    """Aggregate the clinician dashboard into a single read.

    Range bounds sent without an offset are read as wall-clock in the
    clinician's own timezone, so "today" is their midnight-to-midnight.
    """
    # Today's appointments + last-visit dates for exactly those patients —
    # no blind patient-list page (and so none of its patient_viewed rows).
    today_appts = scheduling.list_appointments(user.id, today_start, today_end, tz=tz)
    today_patient_ids = list({a.patient_id for a in today_appts})
    today_patients = patient_repo.get_multiple(today_patient_ids, user.id)
    last_visit_by_patient = {
        pid: (p.last_session_date if (p := today_patients.get(pid)) else None)
        for pid in today_patient_ids
    }

    # Rest-of-week: only the confirmed count is shown.
    week_appts = scheduling.list_appointments(user.id, week_start, week_end, tz=tz)
    week_confirmed_count = sum(1 for a in week_appts if a.status == "confirmed")

    # Session aggregates computed over the full accessible set, not a page.
    status_counts = session_repo.count_by_status(user.id)
    transcription_pending_count = status_counts.get(SessionStatus.QUEUED, 0) + status_counts.get(
        SessionStatus.PROCESSING, 0
    )
    awaiting_review_total = status_counts.get(SessionStatus.PENDING_REVIEW, 0)
    notes_pending_count = notes_repo.count_unfinalized(user.id)

    # The handful of awaiting-review rows actually rendered — a real session
    # disclosure, so audit each one shown.
    recent = session_repo.list_recent_by_status(
        user.id, SessionStatus.PENDING_REVIEW, limit=AWAITING_REVIEW_LIMIT
    )
    review_patients = patient_repo.get_multiple(list({s.patient_id for s in recent}), user.id)
    review_notes = notes_repo.get_by_session_ids([s.id for s in recent], user.id)
    awaiting_review: list[AwaitingReviewItem] = []
    for s in recent:
        patient = review_patients.get(s.patient_id)
        note = review_notes.get(s.id)
        awaiting_review.append(
            AwaitingReviewItem(
                session_id=s.id,
                patient_name=patient.display_name if patient else "Unknown",
                session_date=s.session_date,
                status=str(s.status),
                note_finalized_at=note.finalized_at if note else None,
            )
        )
        audit.log_session_action(AuditAction.SESSION_VIEWED, user, request, s, patient)

    return DashboardSummaryResponse(
        today_appointments=[_to_response(a) for a in today_appts],
        last_visit_by_patient=last_visit_by_patient,
        week_confirmed_count=week_confirmed_count,
        notes_pending_count=notes_pending_count,
        transcription_pending_count=transcription_pending_count,
        awaiting_review_total=awaiting_review_total,
        awaiting_review=awaiting_review,
    )
