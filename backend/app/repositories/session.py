# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Therapy session repository implementations."""

from abc import ABC, abstractmethod
from collections.abc import Collection
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
    def get_multiple(self, session_ids: list[str], user_id: str) -> dict[str, TherapySession]:
        """Get multiple sessions by ID, ensuring the user has patient access.

        One query for many sessions, matching ``PatientRepository.get_multiple``.
        Sessions the user cannot access are simply absent from the result.
        """
        pass

    def session_dates_by_patient(self, patient_id: str, user_id: str) -> list[datetime]:
        """Session dates only, same access gate as :meth:`list_by_patient`.

        For callers that only correlate timestamps (the audit reviewer's
        recent-session check) — backends can answer this without loading
        transcript or note content. Default delegates to
        ``list_by_patient``.
        """
        return [s.session_date for s in self.list_by_patient(patient_id, user_id)]

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
    def count_by_status(self, user_id: str) -> dict[str, int]:
        """Count non-deleted sessions for accessible patients, grouped by status.

        Powers dashboard aggregates (transcripts in flight, notes awaiting
        review) over the full set without paging every session into the app.
        """
        pass

    @abstractmethod
    def list_recent_by_status(
        self, user_id: str, status: str, *, limit: int
    ) -> list[TherapySession]:
        """Most-recent accessible sessions (session_date desc) in one status."""
        pass

    @abstractmethod
    def get_next_session_date(
        self,
        patient_id: str,
        user_id: str,
        *,
        after: datetime,
        exclude_statuses: Collection[str],
    ) -> datetime | None:
        """Earliest upcoming session datetime for a patient, or ``None``.

        Returns ``MIN(COALESCE(scheduled_at, session_date))`` over the
        patient's accessible, non-deleted sessions whose status is not in
        ``exclude_statuses`` and whose effective date is strictly after
        ``after``. Computed in the store so recomputing a patient's next
        appointment on every status change doesn't page their entire
        session history into the app just to take one MIN.
        """
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
    """In-memory implementation of TherapySessionRepository.

    Maintains a per-(patient_id, user_id) access set mirroring
    ``patient_clinicians`` so tests exercise the same access boundary
    as production. ``session.user_id`` stays on the row as actor data
    (who recorded the session) but is not the access proxy.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, TherapySession] = {}
        self._access: set[tuple[str, str]] = set()  # (patient_id, user_id)

    def grant_access(self, patient_id: str, user_id: str) -> None:
        """Test helper: record that ``user_id`` can access ``patient_id``'s sessions."""
        self._access.add((patient_id, user_id))

    def _can_access(self, patient_id: str, user_id: str) -> bool:
        return (patient_id, user_id) in self._access

    def get(self, session_id: str, user_id: str) -> TherapySession | None:
        """Get session by ID; ``None`` if absent or user lacks patient access."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if not self._can_access(session.patient_id, user_id):
            return None
        return session

    def list_by_patient(self, patient_id: str, user_id: str) -> list[TherapySession]:
        if not self._can_access(patient_id, user_id):
            return []
        sessions = [s for s in self._sessions.values() if s.patient_id == patient_id]
        sessions.sort(key=lambda s: s.session_date, reverse=True)
        return sessions

    def get_multiple(self, session_ids: list[str], user_id: str) -> dict[str, TherapySession]:
        wanted = set(session_ids)
        return {
            s.id: s
            for s in self._sessions.values()
            if s.id in wanted and self._can_access(s.patient_id, user_id)
        }

    def list_by_user(
        self, user_id: str, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[TherapySession], int]:
        """List sessions for any patient the user has access to.

        Semantic match for ``PostgresTherapySessionRepository.list_by_user``
        after the access-table migration: returns sessions for *patients*
        the user is granted on, not sessions the user personally
        recorded.
        """
        sessions = [s for s in self._sessions.values() if self._can_access(s.patient_id, user_id)]
        sessions.sort(key=lambda s: s.session_date, reverse=True)
        total = len(sessions)
        offset = (page - 1) * page_size
        return sessions[offset : offset + page_size], total

    def create(self, session: TherapySession) -> TherapySession:
        """Insert a session.

        Auto-grants access to ``session.user_id`` for ``session.patient_id``
        if not already granted — matches the production guarantee that a
        clinician creating a session has been verified to have access to
        the patient (the calling service makes that check via
        ``patient_repo.get(patient_id, user_id)``).
        """
        self._sessions[session.id] = session
        self._access.add((session.patient_id, session.user_id))
        return session

    def update(self, session: TherapySession) -> TherapySession:
        self._sessions[session.id] = session
        return session

    def list_today_by_user(self, user_id: str, tz_name: str = "UTC") -> list[TherapySession]:
        start_utc, end_utc = _compute_day_boundaries(tz_name)
        sessions = [
            s
            for s in self._sessions.values()
            if self._can_access(s.patient_id, user_id)
            and s.scheduled_at is not None
            and start_utc <= s.scheduled_at < end_utc
        ]
        sessions.sort(key=lambda s: s.scheduled_at or datetime.min.replace(tzinfo=UTC))
        return sessions

    def count_by_status(self, user_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in self._sessions.values():
            if self._can_access(s.patient_id, user_id):
                key = str(s.status)
                counts[key] = counts.get(key, 0) + 1
        return counts

    def list_recent_by_status(
        self, user_id: str, status: str, *, limit: int
    ) -> list[TherapySession]:
        sessions = [
            s
            for s in self._sessions.values()
            if s.status == status and self._can_access(s.patient_id, user_id)
        ]
        sessions.sort(key=lambda s: s.session_date, reverse=True)
        return sessions[:limit]

    def get_next_session_date(
        self,
        patient_id: str,
        user_id: str,
        *,
        after: datetime,
        exclude_statuses: Collection[str],
    ) -> datetime | None:
        if not self._can_access(patient_id, user_id):
            return None
        candidates = [
            effective
            for s in self._sessions.values()
            if s.patient_id == patient_id and s.status not in exclude_statuses
            for effective in [s.scheduled_at or s.session_date]
            if effective > after
        ]
        return min(candidates) if candidates else None

    def get_session_number_for_patient(self, patient_id: str) -> int:
        """Get the next session number for a patient."""
        patient_sessions = [s for s in self._sessions.values() if s.patient_id == patient_id]
        if not patient_sessions:
            return 1
        return max(s.session_number for s in patient_sessions) + 1
