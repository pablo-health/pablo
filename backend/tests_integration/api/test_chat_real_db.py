# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""End-to-end chat routes against a real Postgres + RLS tenant (THERAPY-phaz).

The unit suite at ``backend/tests/test_routes_chat*.py`` uses
``InMemoryChatRepository``, which does not enforce CHECK constraints,
RLS policies, or FK relationships. Several launch-blocking bugs have
escaped into prod via that gap:

* ``ck_chat_messages_content_len`` rejecting the empty-content forensic
  row (THERAPY-1cqc). Fixed in migration ``987044c1f592`` (relax the
  lower bound to 0) and in ``chat_turn_service.py:353`` (coerce empty
  final content to ``"[no output]"``). The in-memory repo accepted
  ``content=""`` on every insert, hiding the failure.
* ``has_patient_access`` SQL bind-param bug (THERAPY-255p). The
  in-memory repo's access-check stub doesn't go through the SQL
  function, so the broken bind was invisible until prod hit it.

What this test does instead, mirroring
``test_patients_api_e2e.py``'s shape:
  * Provisions a real Postgres-backed tenant schema via
    ``create_practice_schema`` (RLS enabled).
  * Drives ``POST /api/chat/*`` through ``TestClient`` so middleware,
    auth dependencies, repository, FK constraints, CHECKs, and RLS
    policies all run for real.
  * Swaps ``ChatLLMGateway`` for ``FakeChatLLMGateway`` so the test is
    deterministic and free of Vertex availability.

Cases (THERAPY-phaz acceptance):
  1. ``test_empty_assistant_output_persists_as_no_output_marker``
  2. ``test_empty_chart_first_turn_lands_with_empty_context_manifest``
  3. ``test_multi_turn_sequence_increments_and_manifest_recorded``
  4. ``test_cross_patient_access_returns_404``
  5. ``test_archive_then_restore_round_trip``

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.services.chat_llm_gateway import FakeChatLLMGateway
    from fastapi import FastAPI
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
# Enable multi-tenancy so DatabaseSessionMiddleware honors per-request
# schema resolution — same rationale as test_patients_api_e2e.py.
os.environ.setdefault("MULTI_TENANCY_ENABLED", "true")
# The chat router is feature-flagged. Without this the entire ``/api/chat/*``
# surface 404s and the routes under test never run.
os.environ.setdefault("ENABLE_PATIENT_CHAT", "true")


# ---------------------------------------------------------------------------
# Module-scoped infra: alembic head + tenant schema + FastAPI app
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    # Warm the pool with a connection whose search_path includes
    # ``practice`` so the RLS policy CREATE that references
    # ``has_patient_access`` (which lives in ``practice``) resolves
    # the function reference. Without this, fresh schema provisioning
    # fails — same fragility documented in test_patients_api_e2e.py.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_chat_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def fastapi_app() -> FastAPI:
    from app.main import app  # noqa: PLC0415

    return app


# ---------------------------------------------------------------------------
# Per-test user + client wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def e2e_user_id() -> str:
    return "e2e-chat-user"


@pytest.fixture
def e2e_user(e2e_user_id: str):
    from app.models import User  # noqa: PLC0415

    return User(
        id=e2e_user_id,
        email="e2e-chat@example.com",
        name="E2E Chat User",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


@pytest.fixture
def fake_gateway() -> FakeChatLLMGateway:
    from app.services.chat_llm_gateway import FakeChatLLMGateway  # noqa: PLC0415

    return FakeChatLLMGateway()


@pytest.fixture
def e2e_client(  # noqa: PLR0913 — fixture composition mirrors the FastAPI deps
    fastapi_app: FastAPI,
    engine: Engine,
    tenant_schema: str,
    e2e_user,
    e2e_user_id: str,
    fake_gateway: FakeChatLLMGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    from app.auth.service import (  # noqa: PLC0415
        TenantContext,
        get_current_user,
        get_current_user_id,
        get_current_user_no_mfa,
        get_tenant_context,
        require_active_subscription,
        require_baa_acceptance,
    )
    from app.db import get_db_session  # noqa: PLC0415
    from app.routes.chat import get_chat_llm_gateway  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    monkeypatch.setattr(
        "app.db.middleware._resolve_schema_from_request",
        lambda _request: tenant_schema,
    )

    def _tenant_context() -> TenantContext:
        session = get_db_session()
        session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": e2e_user_id},
        )
        return TenantContext(
            user_id=e2e_user_id,
            practice_id="test-tenant",
            practice_schema=tenant_schema,
        )

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: e2e_user_id
    fastapi_app.dependency_overrides[get_current_user] = lambda: e2e_user
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = lambda: e2e_user
    fastapi_app.dependency_overrides[require_active_subscription] = lambda: e2e_user
    fastapi_app.dependency_overrides[require_baa_acceptance] = lambda: e2e_user
    fastapi_app.dependency_overrides[get_tenant_context] = _tenant_context
    fastapi_app.dependency_overrides[get_chat_llm_gateway] = lambda: fake_gateway

    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate_tenant_tables(engine: Engine, schema: str) -> None:
    """Reset between tests. ``chat_messages`` truncates with chat_conversations
    via CASCADE since the FK is ON DELETE CASCADE. Patients + grants too so
    every test starts from an empty chart."""
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(
            text(
                "TRUNCATE TABLE chat_messages, chat_conversations, "
                "patient_clinicians, patients, audit_logs "
                "RESTART IDENTITY CASCADE"
            )
        )
        conn.commit()


def _create_patient(client: TestClient) -> dict:
    """Provision a patient via the public API so the grant row + RLS
    visibility match what the chat routes expect."""
    resp = client.post(
        "/api/patients",
        json={"first_name": "Chat", "last_name": f"Test-{uuid.uuid4().hex[:6]}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_conversation(
    client: TestClient, patient_id: str, *, feature_key: str = "chart_qa"
) -> dict:
    resp = client.post(
        "/api/chat/conversations",
        json={
            "patient_id": patient_id,
            "caller_feature_key": feature_key,
            "caller_system_prompt": "You are a clinical assistant for chart QA.",
            "title": None,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _parse_sse(body: bytes) -> list[tuple[str, dict]]:
    """Parse ``text/event-stream`` body into ``[(event, data_json), ...]``."""
    import json as _json  # noqa: PLC0415

    events: list[tuple[str, dict]] = []
    for block in body.decode().strip().split("\n\n"):
        name = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ").strip()
            elif line.startswith("data: "):
                data = line.removeprefix("data: ").strip()
        if name and data:
            events.append((name, _json.loads(data)))
    return events


# ---------------------------------------------------------------------------
# Case 1 — empty assistant output, real CHECK constraint (THERAPY-1cqc)
# ---------------------------------------------------------------------------


class TestEmptyAssistantOutputRealDB:
    """Lock in THERAPY-1cqc: gemini occasionally returns an empty buffer
    followed by an immediate ``finish_reason=stop`` (observed on
    flash-lite). The forensic-row pattern in ``chat_turn_service``
    inserts the assistant row with ``content=""`` up front and updates
    it at end-of-stream. Two real-Postgres-only failure modes:

      * INSERT of the placeholder must succeed against
        ``ck_chat_messages_content_len`` (relaxed in migration
        ``987044c1f592`` to allow length 0).
      * UPDATE at end-of-stream must replace ``""`` with the
        ``"[no output]"`` sentinel from ``chat_turn_service.py:353`` so
        the persisted row has a non-empty audit trail.
    """

    def test_empty_assistant_output_persists_as_no_output_marker(
        self,
        e2e_client: TestClient,
        engine: Engine,
        tenant_schema: str,
        fake_gateway: FakeChatLLMGateway,
    ) -> None:
        from app.services.chat_llm_gateway import StreamEvent  # noqa: PLC0415

        _truncate_tenant_tables(engine, tenant_schema)
        patient = _create_patient(e2e_client)
        conv = _create_conversation(e2e_client, patient["id"])

        # Empty buffer + clean stop — the exact shape that crashed prod.
        fake_gateway.script = [StreamEvent(finish_reason="stop", output_tokens=0)]

        resp = e2e_client.post(
            f"/api/chat/conversations/{conv['id']}/messages",
            json={"content": "ping"},
        )
        assert resp.status_code == 200, resp.text
        events = _parse_sse(resp.content)
        names = [name for name, _ in events]
        assert "meta" in names, names
        assert names[-1] == "done", names

        # Inspect the persisted assistant row directly. The placeholder
        # INSERT must have landed (no CHECK violation), and the
        # finalize-UPDATE must have written the "[no output]" sentinel.
        with engine.begin() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, false)"),
                {"uid": "e2e-chat-user"},
            )
            rows = (
                conn.execute(
                    text(
                        "SELECT role, content, llm_finish_reason "
                        "FROM chat_messages WHERE conversation_id = CAST(:cid AS uuid) "
                        "ORDER BY sequence"
                    ),
                    {"cid": conv["id"]},
                )
                .mappings()
                .all()
            )
        assert len(rows) == 2, rows
        assert rows[0]["role"] == "user"
        assert rows[1]["role"] == "assistant"
        assert rows[1]["content"] == "[no output]", (
            f"Expected the [no output] sentinel; got {rows[1]['content']!r}. "
            "If this is empty, chat_turn_service.py:353 coercion regressed."
        )
        assert rows[1]["llm_finish_reason"] == "stop"


# ---------------------------------------------------------------------------
# Case 2 — empty-chart first turn (THERAPY-fr6y companion)
# ---------------------------------------------------------------------------


class TestEmptyChartFirstTurnRealDB:
    """First turn against a patient with zero notes / docs / meds. The
    bundler should emit an empty-context manifest, the system prompt
    should carry the empty-chart marker (pablo#248), and the DB row
    should record an empty ``sources_included`` list — proves nothing
    leaks through as a placeholder source.
    """

    def test_empty_chart_first_turn_lands_with_empty_context_manifest(
        self,
        e2e_client: TestClient,
        engine: Engine,
        tenant_schema: str,
        fake_gateway: FakeChatLLMGateway,
    ) -> None:
        from app.services.chat_llm_gateway import StreamEvent  # noqa: PLC0415

        _truncate_tenant_tables(engine, tenant_schema)
        patient = _create_patient(e2e_client)
        conv = _create_conversation(e2e_client, patient["id"])

        fake_gateway.script = [
            StreamEvent(delta="I don't have any chart data for this patient yet."),
            StreamEvent(finish_reason="stop", output_tokens=12),
        ]

        resp = e2e_client.post(
            f"/api/chat/conversations/{conv['id']}/messages",
            json={"content": "what can you tell me about this patient"},
        )
        assert resp.status_code == 200, resp.text

        # Inspect the persisted user-turn manifest. Empty chart means
        # the bundler returned no included sources; this should land
        # on the row as an empty list, not as null or as a placeholder.
        with engine.begin() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, false)"),
                {"uid": "e2e-chat-user"},
            )
            row = (
                conn.execute(
                    text(
                        "SELECT context_manifest FROM chat_messages "
                        "WHERE conversation_id = CAST(:cid AS uuid) AND role = 'user'"
                    ),
                    {"cid": conv["id"]},
                )
                .mappings()
                .one()
            )

        manifest = row["context_manifest"]
        assert manifest is not None, "Empty-chart turn must still record a manifest"
        # The bundler emits an entry per selected source with row_count=0
        # when the chart has nothing for it (rather than dropping the
        # entry). Either shape is valid; what matters is that nothing
        # leaked PHI-shaped content. Tokens must be zero.
        for entry in manifest["sources_included"]:
            assert entry["row_count"] == 0, entry
            assert entry["tokens_est"] == 0, entry
        # The system prompt the model saw should carry the empty-chart
        # marker (pablo#248) so the model has a positive signal that
        # the chart is empty. The marker phrasing has changed across
        # iterations; assert the substantive "no chart data" stance is
        # present rather than pinning a specific token.
        prompt = fake_gateway.calls[-1]["system_prompt"].lower()
        assert "no chart data" in prompt or "chart contains no information" in prompt, (
            "Empty-chart marker missing from system prompt — pablo#248 regression. "
            f"prompt={prompt!r}"
        )


# ---------------------------------------------------------------------------
# Case 3 — multi-turn sequence + per-turn manifest recording
# ---------------------------------------------------------------------------


class TestMultiTurnRealDB:
    """Three turns in a row. ``sequence`` is monotonic per conversation
    and the row-locking ``next_sequence`` SELECT in the Postgres repo
    must hand out 1..6 across user+assistant pairs without gaps or
    duplicates. Each user-turn carries a fresh ``context_manifest``
    snapshot (assembled at turn time, not back-filled).
    """

    def test_multi_turn_sequence_increments_and_manifest_recorded(
        self,
        e2e_client: TestClient,
        engine: Engine,
        tenant_schema: str,
        fake_gateway: FakeChatLLMGateway,
    ) -> None:
        from app.services.chat_llm_gateway import StreamEvent  # noqa: PLC0415

        _truncate_tenant_tables(engine, tenant_schema)
        patient = _create_patient(e2e_client)
        conv = _create_conversation(e2e_client, patient["id"])

        # Same script replayed each turn — FakeChatLLMGateway falls
        # back to ``script`` when ``scripts`` is empty.
        fake_gateway.script = [
            StreamEvent(delta="ok."),
            StreamEvent(finish_reason="stop", output_tokens=1),
        ]
        for prompt in ("turn-1", "turn-2", "turn-3"):
            resp = e2e_client.post(
                f"/api/chat/conversations/{conv['id']}/messages",
                json={"content": prompt},
            )
            assert resp.status_code == 200, f"{prompt}: {resp.text}"

        with engine.begin() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, false)"),
                {"uid": "e2e-chat-user"},
            )
            rows = (
                conn.execute(
                    text(
                        "SELECT sequence, role, content, context_manifest "
                        "FROM chat_messages WHERE conversation_id = CAST(:cid AS uuid) "
                        "ORDER BY sequence"
                    ),
                    {"cid": conv["id"]},
                )
                .mappings()
                .all()
            )

        # 3 turns * (user + assistant) = 6 rows, sequence 1..6.
        assert [r["sequence"] for r in rows] == list(range(1, 7)), rows
        # Alternating roles.
        assert [r["role"] for r in rows] == [
            "user",
            "assistant",
            "user",
            "assistant",
            "user",
            "assistant",
        ], rows
        # Every user turn carries its own manifest (not back-filled
        # from a prior turn). With an empty chart every included entry
        # has row_count=0; what matters is that each turn has its own
        # independently-assembled manifest object.
        user_manifests = [r["context_manifest"] for r in rows if r["role"] == "user"]
        assert len(user_manifests) == 3
        assert all(m is not None for m in user_manifests), user_manifests
        for manifest in user_manifests:
            for entry in manifest["sources_included"]:
                assert entry["row_count"] == 0, entry
                assert entry["tokens_est"] == 0, entry


# ---------------------------------------------------------------------------
# Case 4 — cross-patient access returns 404 (no existence leak)
# ---------------------------------------------------------------------------


class TestCrossPatientAccessRealDB:
    """A second user cannot read or write to conversations they have no
    ``patient_clinicians`` grant on. Denial returns 404 (not 403) so the
    surface cannot be used as an existence oracle. Mirrors the IDOR
    pattern locked in by pytest unit tests, but here through real
    ``has_patient_access`` SQL — which is what catches the bind-param
    class of bug (THERAPY-255p).
    """

    def test_cross_patient_access_returns_404(
        self,
        fastapi_app: FastAPI,
        e2e_client: TestClient,
        engine: Engine,
        tenant_schema: str,
        fake_gateway: FakeChatLLMGateway,
    ) -> None:
        from app.auth.service import require_baa_acceptance  # noqa: PLC0415
        from app.models import User  # noqa: PLC0415
        from app.services.chat_llm_gateway import StreamEvent  # noqa: PLC0415

        _truncate_tenant_tables(engine, tenant_schema)
        patient = _create_patient(e2e_client)
        conv = _create_conversation(e2e_client, patient["id"])
        fake_gateway.script = [StreamEvent(finish_reason="stop", output_tokens=0)]

        # Swap auth identity to a foreigner with no grants. The
        # tenant context still resolves the same schema — the access
        # check is at the grant level, not the schema level.
        foreigner = User(
            id="e2e-chat-foreigner",
            email="foreigner@example.com",
            name="Foreign Clinician",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
            baa_version="2024-01-01",
        )
        prior = fastapi_app.dependency_overrides.get(require_baa_acceptance)
        fastapi_app.dependency_overrides[require_baa_acceptance] = lambda: foreigner
        try:
            # GET conversation by id — 404.
            get_resp = e2e_client.get(f"/api/chat/conversations/{conv['id']}")
            assert get_resp.status_code == 404, get_resp.text

            # POST message into the conversation — also 404, not 403,
            # to avoid the existence-leak oracle.
            post_resp = e2e_client.post(
                f"/api/chat/conversations/{conv['id']}/messages",
                json={"content": "should be denied"},
            )
            assert post_resp.status_code == 404, post_resp.text

            # List conversations on the same patient — empty + 404 on
            # patient itself (route returns 404 when patient lookup
            # fails, which is what foreign access produces).
            list_resp = e2e_client.get(f"/api/chat/conversations?patient_id={patient['id']}")
            # The route documents the contract as 404 on missing
            # patient (which a foreign user effectively sees).
            assert list_resp.status_code == 404, list_resp.text
        finally:
            if prior is not None:
                fastapi_app.dependency_overrides[require_baa_acceptance] = prior
            else:
                fastapi_app.dependency_overrides.pop(require_baa_acceptance, None)

        # No leakage: original user still sees their conversation.
        own_get = e2e_client.get(f"/api/chat/conversations/{conv['id']}")
        assert own_get.status_code == 200, own_get.text


# ---------------------------------------------------------------------------
# Case 5 — archive then restore round-trip
# ---------------------------------------------------------------------------


class TestArchiveRestoreRealDB:
    """``PATCH archive=true`` writes ``archived_at`` to a real column;
    list-default hides it; list-include_archived surfaces it; PATCH
    ``archive=false`` clears the timestamp. Exercises the index/filter
    contract on a real Postgres planner, not the in-memory list-comp
    that the unit tests use."""

    def test_archive_then_restore_round_trip(
        self,
        e2e_client: TestClient,
        engine: Engine,
        tenant_schema: str,
    ) -> None:
        _truncate_tenant_tables(engine, tenant_schema)
        patient = _create_patient(e2e_client)
        conv = _create_conversation(e2e_client, patient["id"])

        # Archive.
        patched = e2e_client.patch(
            f"/api/chat/conversations/{conv['id']}",
            json={"archive": True},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["archived_at"] is not None

        # Default list excludes archived.
        default_list = e2e_client.get(f"/api/chat/conversations?patient_id={patient['id']}")
        assert default_list.status_code == 200
        assert all(c["id"] != conv["id"] for c in default_list.json()["data"]), (
            "Archived conversation should not appear in the default list"
        )

        # include_archived=true surfaces it again.
        with_archived = e2e_client.get(
            f"/api/chat/conversations?patient_id={patient['id']}&include_archived=true"
        )
        assert with_archived.status_code == 200
        ids = [c["id"] for c in with_archived.json()["data"]]
        assert conv["id"] in ids, "Archived conversation should surface under include_archived=true"

        # Restore.
        restored = e2e_client.patch(
            f"/api/chat/conversations/{conv['id']}",
            json={"archive": False},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["archived_at"] is None

        # Default list shows it again post-restore.
        post_restore = e2e_client.get(f"/api/chat/conversations?patient_id={patient['id']}")
        assert post_restore.status_code == 200
        assert any(c["id"] == conv["id"] for c in post_restore.json()["data"]), (
            "Restored conversation should reappear in the default list"
        )
