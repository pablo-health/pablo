# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The clinician's waitlist routes over HTTP, on the real stack and row policies.

One clinician and a handful of clients: three active, one on hold, one no
longer seen, and one the clinician holds no grant on. The routes are driven
the way the client page and the settings list drive them.

What each test would catch: an on-hold or inactive client let onto the list;
a client the clinician cannot see answering anything but 404; positions
that stop being dense after an insert at the top, a removal or a reorder; a
reorder that accepts an entry not on the caller's list; a pause or an
auto-offer switch that moves the entry; the suggested session type and
modality not read from the client's own visits; and an audit row that
carries the days or times wanted instead of ids.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

os.environ.setdefault("ENVIRONMENT", "development")

_TENANT_EMAIL = "waitlist-routes@example.com"
_CLINICIAN = "3c8a1e5f-7d2b-4a96-9e0c-5b1f8d3a7c24"


class _Seed:
    def __init__(self) -> None:
        self.active = [str(uuid.uuid4()) for _ in range(3)]
        self.on_hold = str(uuid.uuid4())
        self.inactive = str(uuid.uuid4())
        self.stranger = str(uuid.uuid4())


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    """A practice provisioned the production way, mapped to ``_TENANT_EMAIL``."""
    from tests_integration.database.test_tenant_resolution_db import (  # noqa: PLC0415
        _seeded_practice,
    )

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    with _seeded_practice(engine, _TENANT_EMAIL) as schema:
        yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def seed(engine: Engine, tenant_schema: str) -> _Seed:
    s = _Seed()
    now = datetime.now(UTC).replace(microsecond=0)
    statuses = dict.fromkeys(s.active, "active")
    statuses |= {s.on_hold: "on_hold", s.inactive: "inactive", s.stranger: "active"}
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": _CLINICIAN})
        for i, (pid, status) in enumerate(statuses.items()):
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                    "last_name_lower, status, session_count, created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), :first, 'Waiting', lower(:first), 'waiting', "
                    ":status, 0, :now, :now)"
                ),
                {"pid": pid, "first": f"Client{i}", "status": status, "now": now},
            )
            if pid != s.stranger:
                conn.execute(
                    text(
                        "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                        "VALUES (CAST(:pid AS uuid), :u, :u)"
                    ),
                    {"pid": pid, "u": _CLINICIAN},
                )
        # The first active client's visits: an older one in person, the latest
        # by video. Their usual session type is the latest one's.
        for days_ago, session_type, link in (
            (21, "intake", None),
            (7, "individual", "https://video.example/room"),
        ):
            start = now - timedelta(days=days_ago)
            conn.execute(
                text(
                    "INSERT INTO appointments (id, user_id, patient_id, title, start_at, end_at, "
                    "duration_minutes, status, session_type, video_link, is_exception, "
                    "reminder_24h_sent, reminder_1h_sent, created_at) "
                    "VALUES (CAST(:id AS uuid), CAST(:u AS uuid), CAST(:pid AS uuid), 'Session', "
                    ":start, :end, 50, 'completed', :st, :link, false, false, false, :now)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "u": _CLINICIAN,
                    "pid": s.active[0],
                    "start": start,
                    "end": start + timedelta(minutes=50),
                    "st": session_type,
                    "link": link,
                    "now": now,
                },
            )
    return s


@pytest.fixture
def client(
    fastapi_app: FastAPI,
    tenant_schema: str,
    seed: _Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """The real route stack as the clinician, wired as the homework route suite is."""
    from app.auth.providers import VerifiedIdentity  # noqa: PLC0415
    from app.auth.service import (  # noqa: PLC0415
        TenantContext,
        get_current_user,
        get_current_user_id,
        get_current_user_no_mfa,
        get_tenant_context,
        require_active_subscription,
        require_baa_acceptance,
    )
    from app.db import arm_current_user_id, get_db_session  # noqa: PLC0415
    from app.models import User  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    user = User(
        id=_CLINICIAN,
        email=_TENANT_EMAIL,
        name="Waitlist Routes",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )
    identity = VerifiedIdentity(
        provider="test",
        subject_id=_CLINICIAN,
        email=_TENANT_EMAIL,
        email_verified=True,
        mfa_satisfied=True,
        claims={},
    )

    def _stash_identity(request: Request) -> None:
        request.state.verified_identity = identity

    monkeypatch.setattr("app.db.middleware._verify_and_stash_clinician_identity", _stash_identity)

    def _armed_user() -> User:
        arm_current_user_id(get_db_session(), _CLINICIAN)
        return user

    def _tenant_context() -> TenantContext:
        arm_current_user_id(get_db_session(), _CLINICIAN)
        return TenantContext(
            user_id=_CLINICIAN, practice_id="test-tenant", practice_schema=tenant_schema
        )

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: _CLINICIAN
    fastapi_app.dependency_overrides[get_current_user] = _armed_user
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = _armed_user
    fastapi_app.dependency_overrides[require_active_subscription] = _armed_user
    fastapi_app.dependency_overrides[require_baa_acceptance] = _armed_user
    fastapi_app.dependency_overrides[get_tenant_context] = _tenant_context
    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.clear()


def _audit(engine: Engine, schema: str) -> list[tuple[str, str]]:
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL search_path = {schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": _CLINICIAN})
        rows = conn.execute(
            text(
                "SELECT action, coalesce(changes::text, '') FROM audit_logs "
                "WHERE action LIKE 'waitlist%' ORDER BY \"timestamp\""
            )
        ).all()
    return [(r[0], r[1]) for r in rows]


_PREFS = {"days": ["thu", "tue"], "times": ["afternoon"], "modality": "telehealth"}


def _order(client: TestClient) -> list[tuple[str, int]]:
    listed = client.get("/api/waitlist")
    assert listed.status_code == 200, listed.text
    return [(e["patient_id"], e["position"]) for e in listed.json()["entries"]]


def test_the_list_keeps_the_clinicians_order(  # noqa: PLR0915 — one journey through the list
    client: TestClient, seed: _Seed, engine: Engine, tenant_schema: str
) -> None:
    a, b, c = seed.active

    # The client page's defaults come from the client's own visits.
    page = client.get(f"/api/patients/{a}/waitlist")
    assert page.status_code == 200, page.text
    assert page.json() == {
        "entry": None,
        "usual_session_type": "individual",
        "past_modalities": ["in_person", "telehealth"],
    }

    added = client.post(f"/api/patients/{a}/waitlist", json=_PREFS)
    assert added.status_code == 201, added.text
    entry = added.json()
    assert entry["days"] == ["tue", "thu"], "days come back in week order"
    assert entry["session_type"] == "individual", "the usual session type is the default"
    assert entry["position"] == 1
    assert not entry["paused"]
    assert not entry["no_auto_offer"]
    assert client.get(f"/api/patients/{a}/waitlist").json()["entry"]["id"] == entry["id"]

    again = client.post(f"/api/patients/{a}/waitlist", json=_PREFS)
    assert again.status_code == 409, "one open entry per client"

    assert client.post(f"/api/patients/{b}/waitlist", json=_PREFS).status_code == 201
    top = client.post(
        f"/api/patients/{c}/waitlist", json={**_PREFS, "position": 1, "session_type": "couples"}
    )
    assert top.status_code == 201, top.text
    assert top.json()["session_type"] == "couples"
    assert _order(client) == [(c, 1), (a, 2), (b, 3)]

    listed = client.get("/api/waitlist").json()["entries"]
    assert listed[0]["patient_name"] == "Client2 Waiting"
    ids = {e["patient_id"]: e["id"] for e in listed}

    reordered = client.put("/api/waitlist/order", json={"entry_ids": [ids[b], ids[a], ids[c]]})
    assert reordered.status_code == 200, reordered.text
    assert _order(client) == [(b, 1), (a, 2), (c, 3)]

    # Reordering some entries permutes just their places.
    assert (
        client.put("/api/waitlist/order", json={"entry_ids": [ids[c], ids[b]]}).status_code == 200
    )
    assert _order(client) == [(c, 1), (a, 2), (b, 3)]

    stray = client.put("/api/waitlist/order", json={"entry_ids": [ids[a], str(uuid.uuid4())]})
    assert stray.status_code == 409
    twice = client.put("/api/waitlist/order", json={"entry_ids": [ids[a], ids[a]]})
    assert twice.status_code == 409

    paused = client.patch(f"/api/waitlist/{ids[a]}", json={"paused": True})
    assert paused.status_code == 200, paused.text
    assert paused.json()["paused"] is True
    assert paused.json()["position"] == 2, "pausing keeps the place"
    flagged = client.patch(f"/api/waitlist/{ids[a]}", json={"no_auto_offer": True})
    assert flagged.json()["no_auto_offer"] is True
    resumed = client.patch(f"/api/waitlist/{ids[a]}", json={"paused": False, "times": ["morning"]})
    assert resumed.json()["paused"] is False
    assert resumed.json()["times"] == ["morning"]
    assert client.patch(f"/api/waitlist/{ids[a]}", json={}).status_code == 400
    assert client.patch(f"/api/waitlist/{ids[a]}", json={"days": []}).status_code == 422

    removed = client.delete(f"/api/waitlist/{ids[c]}")
    assert removed.status_code == 200, removed.text
    assert removed.json()["position"] == 1
    assert _order(client) == [(a, 1), (b, 2)], "removing closes the gap"
    assert client.delete(f"/api/waitlist/{ids[c]}").status_code == 404

    # Put back where it was, with what it had: how an undo restores an entry.
    restored = client.post(
        f"/api/patients/{c}/waitlist",
        json={
            "days": removed.json()["days"],
            "times": removed.json()["times"],
            "modality": removed.json()["modality"],
            "session_type": removed.json()["session_type"],
            "position": removed.json()["position"],
        },
    )
    assert restored.status_code == 201, restored.text
    assert _order(client) == [(c, 1), (a, 2), (b, 3)]

    audit = _audit(engine, tenant_schema)
    actions = {a for a, _ in audit}
    assert {
        "waitlist_viewed",
        "waitlist_entry_added",
        "waitlist_entry_updated",
        "waitlist_entry_removed",
        "waitlist_reordered",
    } <= actions
    assert all(
        "afternoon" not in changes and "morning" not in changes and '"tue"' not in changes
        for _, changes in audit
    ), "audit rows carry ids and field names, never what the client wants"


def test_only_an_active_client_can_wait(client: TestClient, seed: _Seed) -> None:
    for pid in (seed.on_hold, seed.inactive):
        refused = client.post(f"/api/patients/{pid}/waitlist", json=_PREFS)
        assert refused.status_code == 409, refused.text
        assert "Only active clients can be added to the waitlist." in refused.text
        assert client.get(f"/api/patients/{pid}/waitlist").json()["entry"] is None


def test_a_client_without_a_grant_is_not_found(client: TestClient, seed: _Seed) -> None:
    base = f"/api/patients/{seed.stranger}/waitlist"
    assert client.get(base).status_code == 404
    assert client.post(base, json=_PREFS).status_code == 404


def test_preferences_must_name_days_times_and_a_known_modality(
    client: TestClient, seed: _Seed
) -> None:
    base = f"/api/patients/{seed.active[1]}/waitlist"
    assert client.post(base, json={**_PREFS, "days": []}).status_code == 422
    assert client.post(base, json={**_PREFS, "times": ["noon"]}).status_code == 422
    assert client.post(base, json={**_PREFS, "modality": "phone"}).status_code == 422
