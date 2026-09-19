# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for GET /api/users/me/audit-log and its repository plumbing."""

from datetime import UTC, datetime, timedelta

import pytest
from app.models.audit import (
    ACTOR_TYPE_ANONYMOUS,
    ACTOR_TYPE_CLINICIAN,
    AuditAction,
    AuditCursor,
    AuditLogEntry,
    ResourceType,
)
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


class TestAuditCursor:
    def test_round_trips(self) -> None:
        cursor = AuditCursor(timestamp=datetime(2026, 3, 1, 12, 30, tzinfo=UTC), entry_id="abc")
        assert AuditCursor.decode(cursor.encode()) == cursor

    def test_is_opaque(self) -> None:
        # Nobody should be able to read a timestamp off the wire and start
        # constructing cursors by hand against a shape we may change.
        encoded = AuditCursor(timestamp=datetime(2026, 3, 1, tzinfo=UTC), entry_id="abc").encode()
        assert "2026" not in encoded
        assert "abc" not in encoded

    def test_from_entry_reads_the_entry_timestamp(self) -> None:
        ts = datetime(2026, 3, 1, 12, 30, tzinfo=UTC)
        entry = AuditLogEntry(user_id="u", action="a", timestamp=_iso(ts), id="row-1")
        cursor = AuditCursor.from_entry(entry)
        assert cursor == AuditCursor(timestamp=ts, entry_id="row-1")

    @pytest.mark.parametrize(
        "bad",
        [
            "not-base64!!",
            # Valid base64url, but no id half.
            AuditCursor(timestamp=datetime(2026, 3, 1, tzinfo=UTC), entry_id="x").encode()[:4],
            "",
        ],
    )
    def test_rejects_malformed(self, bad: str) -> None:
        with pytest.raises(ValueError, match=r"^cursor "):
            AuditCursor.decode(bad)


class TestListForUserPaging:
    def _repo_with(self, count: int) -> tuple[InMemoryAuditRepository, datetime]:
        repo = InMemoryAuditRepository()
        base = datetime.now(UTC)
        for i in range(count):
            repo.append(
                AuditLogEntry(
                    user_id="u",
                    action=f"a{i}",
                    timestamp=_iso(base - timedelta(seconds=i)),
                )
            )
        return repo, base

    def test_before_pages_backwards_without_gaps_or_repeats(self) -> None:
        repo, _ = self._repo_with(10)

        first = repo.list_for_user("u", limit=4)
        second = repo.list_for_user("u", limit=4, before=AuditCursor.from_entry(first[-1]))
        third = repo.list_for_user("u", limit=4, before=AuditCursor.from_entry(second[-1]))

        actions = [e.action for e in first + second + third]
        assert actions == [f"a{i}" for i in range(10)]
        assert len(set(actions)) == 10

    def test_ties_on_timestamp_are_not_dropped(self) -> None:
        # Rows written in one transaction share a timestamp to the
        # microsecond. A cursor on timestamp alone loses whichever of them
        # fell on the far side of the page boundary.
        repo = InMemoryAuditRepository()
        shared = _iso(datetime.now(UTC))
        for i in range(4):
            repo.append(AuditLogEntry(user_id="u", action=f"a{i}", timestamp=shared))

        first = repo.list_for_user("u", limit=2)
        second = repo.list_for_user("u", limit=2, before=AuditCursor.from_entry(first[-1]))

        seen = {e.id for e in first + second}
        assert len(seen) == 4

    def test_before_and_since_compose(self) -> None:
        repo, base = self._repo_with(10)
        # Window to the newest five rows, then page inside it.
        since = base - timedelta(seconds=5)
        page = repo.list_for_user("u", since=since, limit=3)
        assert [e.action for e in page] == ["a0", "a1", "a2"]
        rest = repo.list_for_user(
            "u", since=since, limit=3, before=AuditCursor.from_entry(page[-1])
        )
        assert [e.action for e in rest] == ["a3", "a4"]


class TestSelfAuditViewRoute:
    def test_returns_only_caller_rows(
        self,
        client,
        mock_user_id,
        mock_audit_service,  # type: ignore[no-untyped-def]
    ) -> None:
        audit = mock_audit_service
        audit._repo = InMemoryAuditRepository()
        audit._repo.append(AuditLogEntry(user_id=mock_user_id, action="patient_viewed"))
        audit._repo.append(AuditLogEntry(user_id="someone-else", action="patient_viewed"))

        resp = client.get("/api/users/me/audit-log")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["action"] == "patient_viewed"
        # Response should not echo user_id or changes
        assert "user_id" not in body["data"][0]
        assert "changes" not in body["data"][0]
        # A row in the caller's own trail is not necessarily a row the caller
        # wrote — a public booking lands here under an anonymous actor — so
        # the kind of principal has to travel with it.
        assert body["data"][0]["actor_type"] == ACTOR_TYPE_CLINICIAN

    def test_reports_the_actor_type_of_each_row(
        self,
        client,
        mock_user_id,
        mock_audit_service,  # type: ignore[no-untyped-def]
    ) -> None:
        audit = mock_audit_service
        audit._repo = InMemoryAuditRepository()
        audit._repo.append(
            AuditLogEntry(
                user_id=mock_user_id,
                action="patient_created",
                actor_type=ACTOR_TYPE_ANONYMOUS,
            )
        )

        resp = client.get("/api/users/me/audit-log")

        assert resp.status_code == 200
        rows = [r for r in resp.json()["data"] if r["action"] == "patient_created"]
        assert [r["actor_type"] for r in rows] == [ACTOR_TYPE_ANONYMOUS]

    def test_read_is_meta_audited(
        self,
        client,
        mock_user_id,
        mock_audit_service,  # type: ignore[no-untyped-def]
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

    def test_next_cursor_absent_when_the_history_ends(
        self,
        client,
        mock_user_id,
        mock_audit_service,  # type: ignore[no-untyped-def]
    ) -> None:
        audit = mock_audit_service
        audit._repo = InMemoryAuditRepository()
        audit._repo.append(AuditLogEntry(user_id=mock_user_id, action="patient_viewed"))

        body = client.get("/api/users/me/audit-log?limit=50").json()

        # One row against a limit of 50: there is nothing behind it, and the
        # response says so rather than leaving the reader to guess.
        assert body["next_cursor"] is None

    def test_pages_back_past_the_first_screen(
        self,
        client,
        mock_user_id,
        mock_audit_service,  # type: ignore[no-untyped-def]
    ) -> None:
        audit = mock_audit_service
        audit._repo = InMemoryAuditRepository()
        base = datetime.now(UTC)
        for i in range(5):
            audit._repo.append(
                AuditLogEntry(
                    user_id=mock_user_id,
                    action=f"a{i}",
                    timestamp=_iso(base - timedelta(seconds=i)),
                )
            )

        first = client.get("/api/users/me/audit-log?limit=2").json()
        assert [r["action"] for r in first["data"]] == ["a0", "a1"]
        assert first["next_cursor"]

        second = client.get(f"/api/users/me/audit-log?limit=2&cursor={first['next_cursor']}").json()
        assert [r["action"] for r in second["data"]] == ["a2", "a3"]

    def test_reading_does_not_chase_its_own_tail(
        self,
        client,
        mock_user_id,
        mock_audit_service,  # type: ignore[no-untyped-def]
    ) -> None:
        # Each read writes a self-audit-view row. Those land NEWER than the
        # page just served, so paging backwards can never be fed by its own
        # writes — otherwise "load more" would never reach the end.
        audit = mock_audit_service
        audit._repo = InMemoryAuditRepository()
        base = datetime.now(UTC)
        for i in range(3):
            audit._repo.append(
                AuditLogEntry(
                    user_id=mock_user_id,
                    action=f"a{i}",
                    timestamp=_iso(base - timedelta(seconds=i)),
                )
            )

        cursor = client.get("/api/users/me/audit-log?limit=3").json()["next_cursor"]
        assert cursor
        tail = client.get(f"/api/users/me/audit-log?limit=3&cursor={cursor}").json()

        assert tail["data"] == []
        assert tail["next_cursor"] is None

    def test_malformed_cursor_is_rejected(self, client) -> None:  # type: ignore[no-untyped-def]
        resp = client.get("/api/users/me/audit-log?cursor=not-a-cursor")
        assert resp.status_code == 400

    def test_user_id_param_not_accepted(
        self,
        client,
        mock_user_id,  # type: ignore[no-untyped-def]
    ) -> None:
        # Even if a caller passes user_id=other, results are still the caller's own.
        resp = client.get("/api/users/me/audit-log?user_id=other-user")
        assert resp.status_code == 200  # unrecognized param ignored by FastAPI
        # No cross-tenant data returned (repo is empty for our mock user)
        assert resp.json()["data"] == []
