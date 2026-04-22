# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for GET /api/users/me/audit-log and its repository plumbing.

Contract pins:
- Response is scoped to the caller; a user_id query param is not accepted.
- ``changes`` never leaks into the response body.
- The read itself gets audited (``self_audit_viewed`` meta-audit row).
- ``since`` filters strictly after the timestamp; ``limit`` is bounded.
"""

from datetime import UTC, datetime, timedelta

from app.models.audit import AuditAction, AuditLogEntry, ResourceType
from app.repositories.audit import InMemoryAuditRepository


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


class TestListForUser:
    def test_filters_to_calling_user(self) -> None:
        repo = InMemoryAuditRepository()
        repo.append(AuditLogEntry(user_id="alice", action="patient_viewed"))
        repo.append(AuditLogEntry(user_id="bob", action="patient_viewed"))
        repo.append(AuditLogEntry(user_id="alice", action="patient_listed"))

        rows = repo.list_for_user("alice")
        assert len(rows) == 2
        assert {r.action for r in rows} == {"patient_viewed", "patient_listed"}

    def test_newest_first(self) -> None:
        repo = InMemoryAuditRepository()
        base = datetime.now(UTC)
        repo.append(
            AuditLogEntry(user_id="u", action="a1", timestamp=_iso(base - timedelta(hours=1)))
        )
        repo.append(AuditLogEntry(user_id="u", action="a2", timestamp=_iso(base)))
        rows = repo.list_for_user("u")
        assert [r.action for r in rows] == ["a2", "a1"]

    def test_since_is_strictly_after(self) -> None:
        repo = InMemoryAuditRepository()
        t_old = datetime.now(UTC) - timedelta(hours=2)
        t_new = datetime.now(UTC)
        repo.append(AuditLogEntry(user_id="u", action="old", timestamp=_iso(t_old)))
        repo.append(AuditLogEntry(user_id="u", action="new", timestamp=_iso(t_new)))

        rows = repo.list_for_user("u", since=t_old)
        assert [r.action for r in rows] == ["new"]

    def test_limit_cap(self) -> None:
        repo = InMemoryAuditRepository()
        base = datetime.now(UTC)
        for i in range(10):
            repo.append(
                AuditLogEntry(
                    user_id="u",
                    action=f"a{i}",
                    timestamp=_iso(base - timedelta(seconds=i)),
                )
            )
        rows = repo.list_for_user("u", limit=3)
        assert len(rows) == 3
        # Newest three (a0, a1, a2) since base-0 > base-1 > base-2
        assert [r.action for r in rows] == ["a0", "a1", "a2"]


class TestSelfAuditViewRoute:
    """Endpoint wiring — scoping and meta-audit."""

    def test_returns_only_caller_rows(
        self, client, mock_user_id, mock_audit_service  # type: ignore[no-untyped-def]
    ) -> None:
        audit = mock_audit_service
        audit._repo = InMemoryAuditRepository()
        audit._repo.append(
            AuditLogEntry(user_id=mock_user_id, action="patient_viewed")
        )
        audit._repo.append(AuditLogEntry(user_id="someone-else", action="patient_viewed"))

        resp = client.get("/api/users/me/audit-log")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["action"] == "patient_viewed"
        # Response should not echo user_id or changes
        assert "user_id" not in body["data"][0]
        assert "changes" not in body["data"][0]

    def test_read_is_meta_audited(
        self, client, mock_user_id, mock_audit_service  # type: ignore[no-untyped-def]
    ) -> None:
        audit = mock_audit_service
        audit._repo = InMemoryAuditRepository()

        client.get("/api/users/me/audit-log")

        # The act of reading the audit stream is itself audited.
        rows = audit._repo.list_for_user(mock_user_id)
        assert any(r.action == AuditAction.SELF_AUDIT_VIEWED.value for r in rows)
        meta = next(r for r in rows if r.action == AuditAction.SELF_AUDIT_VIEWED.value)
        assert meta.resource_type == ResourceType.SELF.value
        assert meta.resource_id == mock_user_id

    def test_limit_enforced(self, client) -> None:  # type: ignore[no-untyped-def]
        resp = client.get("/api/users/me/audit-log?limit=99999")
        assert resp.status_code == 422  # FastAPI bounds rejection

    def test_user_id_param_not_accepted(
        self, client, mock_user_id  # type: ignore[no-untyped-def]
    ) -> None:
        """Defense: even if a caller passes user_id=other, the route
        ignores it and returns only their own rows."""
        resp = client.get("/api/users/me/audit-log?user_id=other-user")
        assert resp.status_code == 200  # unrecognized param ignored by FastAPI
        # No cross-tenant data returned (repo is empty for our mock user)
        assert resp.json()["data"] == []
