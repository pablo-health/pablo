# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Therapy session repository implementations."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ..models import TherapySession


class TherapySessionRepository(ABC):
    """Abstract base class for therapy session data access."""

    @abstractmethod
    def get(self, session_id: str, user_id: str) -> TherapySession | None:
        """Get session by ID, ensuring it belongs to the user."""
        pass

    @abstractmethod
    def list_by_patient(self, patient_id: str, user_id: str) -> list[TherapySession]:
        """List all therapy sessions for a patient, ensuring user has access."""
        pass

    @abstractmethod
    def list_by_user(
        self, user_id: str, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[TherapySession], int]:
        """List therapy sessions for a user with pagination.

        Returns a tuple of (paginated_sessions, total_count).
        """
        pass

    @abstractmethod
    def create(self, session: TherapySession) -> TherapySession:
        """Create a new therapy session."""
        pass

    @abstractmethod
    def update(self, session: TherapySession) -> TherapySession:
        """Update an existing therapy session."""
        pass

    @abstractmethod
    def list_today_by_user(self, user_id: str, tz_name: str = "UTC") -> list[TherapySession]:
        """List today's sessions for a user, using the given IANA timezone for day boundaries."""
        pass

    @abstractmethod
    def get_session_number_for_patient(self, patient_id: str) -> int:
        """Get the next session number for a patient."""
        pass


def _compute_day_boundaries(tz_name: str) -> tuple[datetime, datetime]:
    """Compute start/end of today in the given timezone, returned as UTC datetimes."""
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    start_of_day = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)
    return start_of_day.astimezone(UTC), end_of_day.astimezone(UTC)


class InMemoryTherapySessionRepository(TherapySessionRepository):
    """In-memory implementation of TherapySessionRepository for testing and development."""

    def __init__(self) -> None:
        self._sessions: dict[str, TherapySession] = {}

    def get(self, session_id: str, user_id: str) -> TherapySession | None:
        """Get session by ID, ensuring it belongs to the user."""
        session = self._sessions.get(session_id)
        if session and session.user_id == user_id:
            return session
        return None

    def list_by_patient(self, patient_id: str, user_id: str) -> list[TherapySession]:
        """List all therapy sessions for a patient, ensuring user has access."""
        sessions = [
            s
            for s in self._sessions.values()
            if s.patient_id == patient_id and s.user_id == user_id
        ]
        # Sort by session date descending (newest first)
        sessions.sort(key=lambda s: s.session_date, reverse=True)
        return sessions

    def list_by_user(
        self, user_id: str, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[TherapySession], int]:
        """List therapy sessions for a user with pagination."""
        sessions = [s for s in self._sessions.values() if s.user_id == user_id]
        sessions.sort(key=lambda s: s.session_date, reverse=True)
        total = len(sessions)
        offset = (page - 1) * page_size
        return sessions[offset : offset + page_size], total

    def create(self, session: TherapySession) -> TherapySession:
        """Create a new therapy session."""
        self._sessions[session.id] = session
        return session

    def update(self, session: TherapySession) -> TherapySession:
        """Update an existing therapy session."""
        self._sessions[session.id] = session
        return session

    def list_today_by_user(self, user_id: str, tz_name: str = "UTC") -> list[TherapySession]:
        """List today's sessions for a user."""
        start_utc, end_utc = _compute_day_boundaries(tz_name)
        sessions = [
            s
            for s in self._sessions.values()
            if s.user_id == user_id
            and s.scheduled_at is not None
            and start_utc <= s.scheduled_at < end_utc
        ]
        sessions.sort(key=lambda s: s.scheduled_at or datetime.min.replace(tzinfo=UTC))
        return sessions

    def get_session_number_for_patient(self, patient_id: str) -> int:
        """Get the next session number for a patient."""
        patient_sessions = [s for s in self._sessions.values() if s.patient_id == patient_id]
        if not patient_sessions:
            return 1
        return max(s.session_number for s in patient_sessions) + 1
