# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice Mode business logic — topic catalog, session lifecycle, rate limiting."""

import json
import logging
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from ..models import Patient, SessionStatus, TherapySession, Transcript
from ..models.enums import SessionSource
from ..models.practice import CreatePracticeSessionRequest, PracticeTopic
from ..repositories import PatientRepository, TherapySessionRepository
from ..settings import Settings
from .practice_session_manager import ConversationEntry

logger = logging.getLogger(__name__)

_TOPICS_PATH = Path(__file__).parent.parent / "data" / "practice_topics.json"


def format_conversation_as_transcript(
    conversation_history: list[ConversationEntry],
) -> str:
    """Format practice conversation_history as a bracketed-timestamp transcript.

    Output format matches what the SOAP parser expects:
        [00:00:08]
        Therapist: Hello, how are you feeling today?
        [00:00:15]
        Client: I've been struggling with anxiety lately...
    """
    lines: list[str] = []
    for entry in conversation_history:
        text = str(entry.get("text", "")).strip()
        if not text:
            continue
        elapsed = float(entry.get("elapsed", 0.0))
        h = int(elapsed // 3600)
        m = int((elapsed % 3600) // 60)
        s = int(elapsed % 60)
        speaker = "Therapist" if entry["role"] == "therapist" else "Client"
        lines.append(f"[{h:02d}:{m:02d}:{s:02d}]")
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


class PracticeNotEnabledError(Exception):
    """Practice mode is disabled."""


class PracticeTopicNotFoundError(Exception):
    """Requested topic does not exist."""


class PracticeDailyLimitError(Exception):
    """User has exceeded daily practice session limit."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"Daily practice session limit exceeded ({limit}/{limit}).")


class PracticeConcurrentLimitError(Exception):
    """User already has an active practice session."""


class PracticeSessionNotFoundError(Exception):
    """Practice session not found or not owned by user."""


class PracticeSessionNotEndableError(Exception):
    """Session is not in a state that can be ended."""

    def __init__(self, current_status: str) -> None:
        self.current_status = current_status
        super().__init__(f"Cannot end session with status '{current_status}'")


@lru_cache(maxsize=1)
def _load_topics_cached() -> list[PracticeTopic]:
    raw = json.loads(_TOPICS_PATH.read_text())
    return [PracticeTopic.model_validate(t) for t in raw]


def _load_topics(*, use_cache: bool = True) -> list[PracticeTopic]:
    if use_cache:
        return _load_topics_cached()
    raw = json.loads(_TOPICS_PATH.read_text())
    return [PracticeTopic.model_validate(t) for t in raw]


class PracticeService:
    """Practice session lifecycle management."""

    def __init__(
        self,
        session_repo: TherapySessionRepository,
        patient_repo: PatientRepository,
        settings: Settings,
    ) -> None:
        self._session_repo = session_repo
        self._patient_repo = patient_repo
        self._settings = settings

    # --- Topic catalog ---

    def get_topics(self) -> list[PracticeTopic]:
        return _load_topics(use_cache=not self._settings.is_development)

    def get_topic(self, topic_id: str) -> PracticeTopic | None:
        for t in self.get_topics():
            if t.id == topic_id:
                return t
        return None

    # --- Pablo Bear patient ---

    def _ensure_pablo_patient(self, user_id: str) -> Patient:
        """Get or create the synthetic practice patient for this user."""
        patient_id = f"practice-{user_id}"
        existing = self._patient_repo.get(patient_id, user_id)
        if existing:
            # Migrate old "Pablo" → "Pablo Practice" if needed
            if existing.first_name == "Pablo":
                existing.first_name = "Pablo Practice"
                existing.first_name_lower = "pablo practice"
                existing.updated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                return self._patient_repo.update(existing)
            return existing

        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        patient = Patient(
            id=patient_id,
            user_id=user_id,
            first_name="Pablo Practice",
            last_name="Bear",
            first_name_lower="pablo practice",
            last_name_lower="bear",
            created_at=now,
            updated_at=now,
            status="active",
        )
        return self._patient_repo.create(patient)

    # --- Rate limiting ---

    def _count_today_sessions(self, user_id: str, patient_id: str) -> int:
        """Count practice sessions created today (UTC)."""
        sessions = self._session_repo.list_by_patient(patient_id, user_id)
        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        today_iso = today_start.isoformat().replace("+00:00", "Z")
        return sum(
            1 for s in sessions if s.source == SessionSource.PRACTICE and s.created_at >= today_iso
        )

    def _has_active_session(self, user_id: str, patient_id: str) -> bool:
        """Check if user has an in-progress practice session.

        SCHEDULED sessions older than 5 minutes are considered abandoned
        (e.g. the WebSocket never connected) and auto-cancelled.
        """
        sessions = self._session_repo.list_by_patient(patient_id, user_id)
        active_statuses = {SessionStatus.SCHEDULED, SessionStatus.IN_PROGRESS}
        now = datetime.now(UTC)
        stale_threshold_seconds = 300  # 5 minutes

        for s in sessions:
            if s.source != SessionSource.PRACTICE or s.status not in active_statuses:
                continue

            # Auto-cancel stale SCHEDULED sessions that never started
            if s.status == SessionStatus.SCHEDULED and s.created_at:
                created = datetime.fromisoformat(s.created_at.replace("Z", "+00:00"))
                if (now - created).total_seconds() > stale_threshold_seconds:
                    logger.info("Auto-cancelling stale practice session %s", s.id)
                    s.status = SessionStatus.CANCELLED
                    self._session_repo.update(s)
                    continue

            return True
        return False

    # --- Session CRUD ---

    def create_session(
        self, user_id: str, request: CreatePracticeSessionRequest
    ) -> tuple[TherapySession, PracticeTopic]:
        """Create a practice session. Enforces rate limits."""
        topic = self.get_topic(request.topic_id)
        if not topic:
            raise PracticeTopicNotFoundError

        patient = self._ensure_pablo_patient(user_id)

        # Rate limits
        today_count = self._count_today_sessions(user_id, patient.id)
        if today_count >= self._settings.practice_daily_session_limit:
            raise PracticeDailyLimitError(self._settings.practice_daily_session_limit)

        if self._has_active_session(user_id, patient.id):
            raise PracticeConcurrentLimitError

        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        session_number = self._session_repo.get_session_number_for_patient(patient.id)

        session = TherapySession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            patient_id=patient.id,
            session_date=now,
            session_number=session_number,
            status=SessionStatus.SCHEDULED,
            transcript=Transcript(format="txt", content=""),
            created_at=now,
            source=SessionSource.PRACTICE,
            notes=f"topic_id={request.topic_id};mode={request.mode.value}",
        )
        self._session_repo.create(session)
        return session, topic

    def list_sessions(
        self, user_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[list[TherapySession], int]:
        """List practice sessions for a user."""
        patient_id = f"practice-{user_id}"
        all_sessions = self._session_repo.list_by_patient(patient_id, user_id)
        practice = [s for s in all_sessions if s.source == SessionSource.PRACTICE]
        practice.sort(key=lambda s: s.created_at, reverse=True)
        total = len(practice)
        start = (page - 1) * page_size
        return practice[start : start + page_size], total

    def get_session(self, session_id: str, user_id: str) -> TherapySession | None:
        session = self._session_repo.get(session_id, user_id)
        if session and session.source == SessionSource.PRACTICE:
            return session
        return None

    def start_session(self, session_id: str, user_id: str) -> TherapySession:
        """Transition session to in_progress."""
        session = self._session_repo.get(session_id, user_id)
        if not session or session.source != SessionSource.PRACTICE:
            raise PracticeSessionNotFoundError

        if session.status != SessionStatus.SCHEDULED:
            raise PracticeSessionNotEndableError(session.status)

        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        session.status = SessionStatus.IN_PROGRESS
        session.started_at = now
        session.updated_at = now
        return self._session_repo.update(session)

    def end_session(self, session_id: str, user_id: str) -> TherapySession:
        """End a practice session, transitioning to recording_complete."""
        session = self._session_repo.get(session_id, user_id)
        if not session or session.source != SessionSource.PRACTICE:
            raise PracticeSessionNotFoundError

        endable = {SessionStatus.SCHEDULED, SessionStatus.IN_PROGRESS}
        if session.status not in endable:
            raise PracticeSessionNotEndableError(session.status)

        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        session.status = SessionStatus.RECORDING_COMPLETE
        session.ended_at = now
        session.updated_at = now

        if session.started_at:
            started = datetime.fromisoformat(session.started_at.replace("Z", "+00:00"))
            ended = datetime.fromisoformat(now.replace("Z", "+00:00"))
            session.duration_minutes = int((ended - started).total_seconds() // 60)

        return self._session_repo.update(session)
