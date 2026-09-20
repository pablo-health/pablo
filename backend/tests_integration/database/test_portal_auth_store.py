# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Real-Postgres proof for the portal sign-in stores.

The route tests run the real handlers against in-memory stores. These run the
real stores against a real database, which is where four things can only be
proven:

* **Provisioning.** A FRESHLY provisioned practice must already have both
  tables. That is the template-regen obligation with teeth: skip the regen
  and this file fails with ``relation "companion_auth_challenges" does not
  exist`` — the same way it would fail in production, but here rather than in
  whatever unrelated test happens to provision a practice next.
* **The not-row-scoped classification.** Both tables carry ``patient_id``, so
  ``enable_rls_on_schema`` reaches them and would force row-level security on
  them with no policy. Provisioning raises on that, so a missing entry in
  ``_CORE_NOT_ROW_SCOPED`` shows up as a provisioning failure in the fixture
  below — and the state it should produce is asserted directly, so a future
  change that force-enables with some policy fails here with the reason
  rather than downstream with a deny-all.
* **The SQL itself.** ``increment_attempts`` returns its new value from an
  ``UPDATE ... RETURNING``, and the two revoke sweeps are set-based; none of
  that is exercised by a dict-backed double.
* **The two producers agree.** A freshly provisioned practice gets these
  tables from ``tenant_template.sql``; an existing one gets them from the
  revision. Those are different code paths, and the classic failure is that
  one ships and the other does not.

Isolation is the schema boundary, so one test provisions a second practice
and checks that identical token ids in each are separate rows.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from app.portal.db_store import DbPortalAuthStore, DbPortalSessionStore
from app.portal.store import InviteChallenge, PortalSessionRecord
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

_CHALLENGES = "companion_auth_challenges"
_SESSIONS = "companion_sessions"

_PATIENT_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_PATIENT_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_NOW = int(time.time())

# The revision this one follows. Rolling a schema back to it and forward
# again replays exactly the revision under test.
_PARENT_REVISION = "a71c5e09d4b3"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _new_schema(engine: Engine, label: str) -> str:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    # Warm the pool so policy CREATEs referencing ``has_patient_access``
    # (which lives in ``practice``) resolve.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_{label}_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    return schema


def _drop_schema(engine: Engine, schema: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    schema = _new_schema(engine, "portal")
    yield schema
    _drop_schema(engine, schema)


def _scoped(engine: Engine, schema: str) -> Session:
    session = Session(engine)
    session.execute(text(f"SET search_path = {schema}, platform, public"))
    return session


def _challenge(
    jti: str, *, patient_id: str = _PATIENT_A, tenant: str = "practice", expires_in: int = 900
) -> InviteChallenge:
    return InviteChallenge(
        jti=jti,
        patient_id=patient_id,
        tenant=tenant,
        otp_hash=f"hash-of-{jti}",
        expires_at=_NOW + expires_in,
    )


def _session_record(
    jti: str, *, patient_id: str = _PATIENT_A, ttl: int = 3600, chain_started_at: int = _NOW
) -> PortalSessionRecord:
    return PortalSessionRecord(
        jti=jti,
        patient_id=patient_id,
        issued_at=_NOW,
        expires_at=_NOW + ttl,
        chain_started_at=chain_started_at,
    )


@pytest.fixture
def challenges(engine: Engine, tenant_schema: str) -> Iterator[DbPortalAuthStore]:
    with _scoped(engine, tenant_schema) as session:
        yield DbPortalAuthStore(session, tenant=tenant_schema)
        session.rollback()


@pytest.fixture
def sessions(engine: Engine, tenant_schema: str) -> Iterator[DbPortalSessionStore]:
    with _scoped(engine, tenant_schema) as session:
        yield DbPortalSessionStore(session)
        session.rollback()


# ---------------------------------------------------------------------------
# Invite challenges
# ---------------------------------------------------------------------------


def test_challenge_round_trips(challenges: DbPortalAuthStore) -> None:
    challenges.put_challenge(_challenge("jti-round-trip"))

    stored = challenges.get_challenge("jti-round-trip")

    assert stored is not None
    assert stored.patient_id == _PATIENT_A
    assert stored.otp_hash == "hash-of-jti-round-trip"
    assert stored.attempts == 0
    assert stored.consumed is False
    # Seconds survive the TIMESTAMPTZ round trip, which the TTL checks in the
    # service depend on.
    assert stored.expires_at == _NOW + 900


def test_challenge_tenant_comes_from_the_binding_not_the_row(
    engine: Engine, tenant_schema: str
) -> None:
    """There is no tenant column — the schema IS the scope. A row must
    therefore never be able to name a practice other than the one it was read
    out of."""
    with _scoped(engine, tenant_schema) as session:
        store = DbPortalAuthStore(session, tenant="practice_somewhere_else")
        store.put_challenge(_challenge("jti-binding", tenant="a-claim-we-ignore"))

        stored = store.get_challenge("jti-binding")
        assert stored is not None
        assert stored.tenant == "practice_somewhere_else"
        session.rollback()


def test_unknown_challenge_is_none(challenges: DbPortalAuthStore) -> None:
    assert challenges.get_challenge("no-such-jti") is None


def test_increment_attempts_returns_the_new_total(challenges: DbPortalAuthStore) -> None:
    challenges.put_challenge(_challenge("jti-attempts"))

    assert challenges.increment_attempts("jti-attempts") == 1
    assert challenges.increment_attempts("jti-attempts") == 2

    stored = challenges.get_challenge("jti-attempts")
    assert stored is not None
    assert stored.attempts == 2


def test_mark_consumed_is_what_makes_an_invitation_single_use(
    challenges: DbPortalAuthStore,
) -> None:
    challenges.put_challenge(_challenge("jti-consume"))
    challenges.mark_consumed("jti-consume")

    stored = challenges.get_challenge("jti-consume")
    assert stored is not None
    assert stored.consumed is True


def test_consume_outstanding_burns_only_this_patients_live_invitations(
    challenges: DbPortalAuthStore,
) -> None:
    challenges.put_challenge(_challenge("jti-live-a1"))
    challenges.put_challenge(_challenge("jti-live-a2"))
    challenges.put_challenge(_challenge("jti-live-b", patient_id=_PATIENT_B))
    challenges.put_challenge(_challenge("jti-already"))
    challenges.mark_consumed("jti-already")

    burned = challenges.consume_outstanding(_PATIENT_A)

    assert burned == 2
    other = challenges.get_challenge("jti-live-b")
    assert other is not None
    assert other.consumed is False


def test_has_outstanding_ignores_consumed_and_expired(
    challenges: DbPortalAuthStore,
) -> None:
    assert challenges.has_outstanding(_PATIENT_A) is False

    challenges.put_challenge(_challenge("jti-expired", expires_in=-60))
    assert challenges.has_outstanding(_PATIENT_A) is False

    challenges.put_challenge(_challenge("jti-outstanding"))
    assert challenges.has_outstanding(_PATIENT_A) is True

    challenges.mark_consumed("jti-outstanding")
    assert challenges.has_outstanding(_PATIENT_A) is False


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def test_session_round_trips(sessions: DbPortalSessionStore) -> None:
    sessions.record(_session_record("sjti-round-trip"))

    stored = sessions.get("sjti-round-trip")

    assert stored is not None
    assert stored.patient_id == _PATIENT_A
    assert stored.revoked_at is None
    assert stored.chain_started_at == _NOW
    assert stored.is_live(now=_NOW) is True


def test_revoke_stops_a_session_being_live(sessions: DbPortalSessionStore) -> None:
    sessions.record(_session_record("sjti-revoke"))
    sessions.revoke("sjti-revoke", at=_NOW + 10)

    stored = sessions.get("sjti-revoke")
    assert stored is not None
    assert stored.revoked_at == _NOW + 10
    assert stored.is_live(now=_NOW + 20) is False


def test_revoke_keeps_the_first_revocation_time(sessions: DbPortalSessionStore) -> None:
    """When access actually ended is a compliance fact; a second revoke must
    not rewrite it."""
    sessions.record(_session_record("sjti-twice"))
    sessions.revoke("sjti-twice", at=_NOW + 10)
    sessions.revoke("sjti-twice", at=_NOW + 500)

    stored = sessions.get("sjti-twice")
    assert stored is not None
    assert stored.revoked_at == _NOW + 10


def test_expired_session_is_not_live(sessions: DbPortalSessionStore) -> None:
    sessions.record(_session_record("sjti-expired", ttl=-1))

    stored = sessions.get("sjti-expired")
    assert stored is not None
    assert stored.is_live(now=_NOW) is False


def test_revoke_all_for_patient_is_the_kill_switch(
    sessions: DbPortalSessionStore,
) -> None:
    sessions.record(_session_record("sjti-kill-1"))
    sessions.record(_session_record("sjti-kill-2"))
    sessions.record(_session_record("sjti-other", patient_id=_PATIENT_B))

    revoked = sessions.revoke_all_for_patient(_PATIENT_A, at=_NOW + 5)

    assert revoked == 2
    assert sessions.live_count_for_patient(_PATIENT_A, now=_NOW + 5) == 0
    assert sessions.live_count_for_patient(_PATIENT_B, now=_NOW + 5) == 1
    # Idempotent: a second sweep has nothing left to revoke.
    assert sessions.revoke_all_for_patient(_PATIENT_A, at=_NOW + 6) == 0


def test_live_count_excludes_expired_and_revoked(
    sessions: DbPortalSessionStore,
) -> None:
    sessions.record(_session_record("sjti-count-live"))
    sessions.record(_session_record("sjti-count-expired", ttl=-1))
    sessions.record(_session_record("sjti-count-revoked"))
    sessions.revoke("sjti-count-revoked", at=_NOW)

    assert sessions.live_count_for_patient(_PATIENT_A, now=_NOW) == 1


def test_rotation_leaves_exactly_one_live_session(
    sessions: DbPortalSessionStore,
) -> None:
    """What refresh does, at the store level: the replacement carries the
    chain start forward and the old row is retired."""
    sessions.record(_session_record("sjti-old"))
    sessions.revoke("sjti-old", at=_NOW + 30)
    sessions.record(_session_record("sjti-new", chain_started_at=_NOW))

    assert sessions.live_count_for_patient(_PATIENT_A, now=_NOW + 30) == 1
    fresh = sessions.get("sjti-new")
    assert fresh is not None
    assert fresh.chain_started_at == _NOW


# ---------------------------------------------------------------------------
# Provisioning + isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", [_CHALLENGES, _SESSIONS])
def test_provisioning_leaves_row_level_security_off(
    engine: Engine, tenant_schema: str, table: str
) -> None:
    """Asserted rather than inferred.

    Both tables carry ``patient_id``, so ``enable_rls_on_schema`` reaches
    them — and the clinician ``has_patient_access`` policy it would apply
    cannot be satisfied by the redemption path, which runs before any
    principal exists. The not-row-scoped classification is what turns that
    into row-level security off; this is the state it should produce.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :s AND c.relname = :t"
            ),
            {"s": tenant_schema, "t": table},
        ).one_or_none()

    assert row is not None, f"{table} should exist in a freshly provisioned practice"
    row_security, force = row
    assert row_security is False, f"{table} should have RLS off (the schema is the boundary)"
    assert force is False, f"{table} should not force RLS"


def test_a_freshly_provisioned_practice_can_hold_both_rows(
    engine: Engine, tenant_schema: str
) -> None:
    with _scoped(engine, tenant_schema) as session:
        store = DbPortalAuthStore(session, tenant=tenant_schema)
        store.put_challenge(_challenge("jti-fresh", tenant=tenant_schema))
        sessions = DbPortalSessionStore(session)
        sessions.record(_session_record("sjti-fresh"))

        assert store.get_challenge("jti-fresh") is not None
        assert sessions.get("sjti-fresh") is not None
        session.rollback()


def test_the_same_token_id_in_two_practices_is_two_rows(engine: Engine) -> None:
    """Isolation is the schema, so a token-id collision across practices is
    not a collision at all — and neither practice can see the other's row."""
    shared_jti = "jti-same-in-both"
    first_schema = _new_schema(engine, "portal_iso_a")
    second_schema = _new_schema(engine, "portal_iso_b")
    try:
        with _scoped(engine, first_schema) as first:
            DbPortalAuthStore(first, tenant=first_schema).put_challenge(
                _challenge(shared_jti, patient_id=_PATIENT_A)
            )
            first.commit()

        with _scoped(engine, second_schema) as second:
            store_b = DbPortalAuthStore(second, tenant=second_schema)
            assert store_b.get_challenge(shared_jti) is None

            store_b.put_challenge(_challenge(shared_jti, patient_id=_PATIENT_B))
            stored_b = store_b.get_challenge(shared_jti)
            assert stored_b is not None
            assert stored_b.patient_id == _PATIENT_B
            second.commit()

        with _scoped(engine, first_schema) as first_again:
            stored_a = DbPortalAuthStore(first_again, tenant=first_schema).get_challenge(shared_jti)
            assert stored_a is not None
            assert stored_a.patient_id == _PATIENT_A
    finally:
        _drop_schema(engine, first_schema)
        _drop_schema(engine, second_schema)


# ---------------------------------------------------------------------------
# The template and the chain must agree
# ---------------------------------------------------------------------------


def _shape(engine: Engine, schema: str, table: str) -> dict[str, list[str]]:
    """Columns, indexes, constraints and policy names for *table* in *schema*."""
    with engine.connect() as conn:
        columns = list(
            conn.execute(
                text(
                    "SELECT column_name || ' ' || data_type || ' ' || is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t "
                    "ORDER BY column_name"
                ),
                {"s": schema, "t": table},
            )
            .scalars()
            .all()
        )
        indexes = list(
            conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = :s AND tablename = :t ORDER BY indexname"
                ),
                {"s": schema, "t": table},
            )
            .scalars()
            .all()
        )
        constraints = list(
            conn.execute(
                text(
                    "SELECT con.conname || ' ' || con.contype::text "
                    "FROM pg_constraint con "
                    "JOIN pg_class c ON c.oid = con.conrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname = :t "
                    "ORDER BY con.conname"
                ),
                {"s": schema, "t": table},
            )
            .scalars()
            .all()
        )
        policies = list(
            conn.execute(
                text(
                    "SELECT policyname FROM pg_policies "
                    "WHERE schemaname = :s AND tablename = :t ORDER BY policyname"
                ),
                {"s": schema, "t": table},
            )
            .scalars()
            .all()
        )
    return {
        "columns": columns,
        "indexes": indexes,
        "constraints": constraints,
        "policies": policies,
    }


def _rebuild_through_the_chain(engine: Engine, schema: str) -> None:
    """Drop both tables, roll the schema back one revision, migrate forward.

    This is how an existing practice gets them: not from the template but
    from the revision, replayed here over a schema that does not have them.
    """
    from app.db.migrate_tenants import upgrade_tenant_schema  # noqa: PLC0415

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text(f"DROP TABLE IF EXISTS {schema}.{_SESSIONS} CASCADE"))
        conn.execute(text(f"DROP TABLE IF EXISTS {schema}.{_CHALLENGES} CASCADE"))
        conn.execute(
            text(f"UPDATE {schema}.alembic_version SET version_num = :r"),  # noqa: S608
            {"r": _PARENT_REVISION},
        )

    result = upgrade_tenant_schema(engine, schema)
    assert result.status.value == "success", result.detail


def _reapply_policies(engine: Engine, schema: str) -> None:
    """Re-run the provisioning RLS step, as ``create_practice_schema`` does."""
    from app.db import PLATFORM_SCHEMA, enable_rls_on_schema  # noqa: PLC0415

    with Session(engine) as session:
        session.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        enable_rls_on_schema(session, schema)


@pytest.fixture(scope="module")
def fresh_schema(engine: Engine) -> Iterator[str]:
    """A practice built the way provisioning builds one: from the template."""
    schema = _new_schema(engine, "portal_fresh")
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture(scope="module")
def migrated_schema(engine: Engine) -> Iterator[str]:
    """A practice that got the tables from the revision instead."""
    schema = _new_schema(engine, "portal_migrated")
    _rebuild_through_the_chain(engine, schema)
    _reapply_policies(engine, schema)
    yield schema
    _drop_schema(engine, schema)


class TestFreshAndMigratedAgree:
    """The two producers of these tables must land the same shape.

    If they drift, the symptom appears wherever a fresh practice is next
    provisioned, not here — so they are compared directly.
    """

    @pytest.mark.parametrize("table", [_CHALLENGES, _SESSIONS])
    def test_template_and_chain_produce_the_same_table(
        self, engine: Engine, fresh_schema: str, migrated_schema: str, table: str
    ) -> None:
        fresh = _shape(engine, fresh_schema, table)
        migrated = _shape(engine, migrated_schema, table)
        assert fresh["columns"], f"{table} absent from the template-built schema"
        assert migrated["columns"], f"{table} absent from the chain-built schema"
        assert fresh == migrated

    def test_the_revision_is_idempotent(self, engine: Engine, migrated_schema: str) -> None:
        """Fanned out once per practice schema, so replaying it must be a no-op."""
        before = _shape(engine, migrated_schema, _SESSIONS)
        _rebuild_through_the_chain(engine, migrated_schema)
        _reapply_policies(engine, migrated_schema)
        assert _shape(engine, migrated_schema, _SESSIONS) == before
