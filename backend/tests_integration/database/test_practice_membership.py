# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""An account's practice membership decides where it signs in, against Postgres.

A signed-in request resolves the account's membership
(``platform.practice_members``) first. The email mapping decides only for an
account without one: that first request, when its address was verified by the
sign-in token, records the mapping's answer as the account's membership.
Callers that pass the address stored on the account never write. A membership
that names a different practice than the address maps to wins, and the
disagreement is logged with ids only.

Nothing on the resolution path is patched except Redis (off, or the in-memory
stand-in the cache's own suite uses) and the request session's RLS arm, which
needs a live request. The rows are the ones the code reads and writes.

Run: ``make test-integration``.
"""

from __future__ import annotations

import contextvars
import logging
import os
import threading
import time
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session
from tests.practice_resolution_fakes import FakeRedis

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from datetime import datetime

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)


class _World:
    """Practices, accounts and mappings made by one test, removed after it."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.label = uuid.uuid4().hex[:8]
        self.practice_ids: list[str] = []
        self.user_ids: list[str] = []
        self.emails: list[str] = []

    def practice(self, name: str, *, active: bool = True) -> SimpleNamespace:
        from app.db.platform_models import PracticeRow  # noqa: PLC0415
        from app.utcnow import utc_now  # noqa: PLC0415

        practice_id = f"members-{name}-{self.label}"
        schema = f"practice_test_members_{name}_{self.label}"
        with Session(self.engine) as session:
            session.add(
                PracticeRow(
                    id=practice_id,
                    name=f"Members {name}",
                    schema_name=schema,
                    owner_email=f"owner-{name}-{self.label}@example.invalid",
                    created_at=utc_now(),
                    is_active=active,
                )
            )
            session.commit()
        self.practice_ids.append(practice_id)
        return SimpleNamespace(id=practice_id, schema=schema)

    def account(
        self, name: str, *, subject_id: str | None = None, stored_email: str | None = None
    ) -> SimpleNamespace:
        """A platform user, and its identity row when ``subject_id`` is given.

        ``stored_email`` is what ``users.email`` holds, when it differs from
        the lowercased address the account signs in with.
        """
        from app.db.platform_models import PlatformUserRow, UserIdentityRow  # noqa: PLC0415
        from app.utcnow import utc_now  # noqa: PLC0415

        user_id = str(uuid.uuid4())
        email = f"{name}-{self.label}@example.invalid".lower()
        with Session(self.engine) as session:
            session.add(
                PlatformUserRow(
                    id=user_id, email=stored_email or email, name=name, created_at=utc_now()
                )
            )
            session.flush()
            if subject_id is not None:
                session.add(
                    UserIdentityRow(
                        provider="firebase",
                        subject_id=subject_id,
                        user_id=user_id,
                        linked_at=utc_now(),
                    )
                )
            session.commit()
        self.user_ids.append(user_id)
        self.emails.append(email)
        return SimpleNamespace(id=user_id, email=email)

    def account_in_request(self, name: str, session: Session) -> SimpleNamespace:
        """A platform user flushed on ``session`` and not committed.

        What ``_resolve_user`` leaves behind on the request that creates an
        account: the row is visible to that session and to no other until the
        request commits.
        """
        from app.db.platform_models import PlatformUserRow  # noqa: PLC0415
        from app.utcnow import utc_now  # noqa: PLC0415

        user_id = str(uuid.uuid4())
        email = f"{name}-{self.label}@example.invalid".lower()
        session.add(PlatformUserRow(id=user_id, email=email, name=name, created_at=utc_now()))
        session.flush()
        self.user_ids.append(user_id)
        self.emails.append(email)
        return SimpleNamespace(id=user_id, email=email)

    def map(self, email: str, practice_id: str) -> None:
        from app.db.platform_models import EmailTenantMappingRow  # noqa: PLC0415
        from app.utcnow import utc_now  # noqa: PLC0415

        with Session(self.engine) as session:
            session.add(
                EmailTenantMappingRow(
                    email=email,
                    tenant_id=practice_id,
                    practice_id=practice_id,
                    created_at=utc_now(),
                )
            )
            session.commit()
        self.emails.append(email)

    def unmap(self, email: str) -> None:
        from app.db.platform_models import EmailTenantMappingRow  # noqa: PLC0415

        with Session(self.engine) as session:
            session.execute(
                delete(EmailTenantMappingRow).where(EmailTenantMappingRow.email == email)
            )
            session.commit()

    def join(
        self,
        user_id: str,
        practice_id: str,
        *,
        via: str = "invite",
        claimed_email: str | None = None,
    ) -> None:
        from app.db.platform_models import PracticeMemberRow  # noqa: PLC0415
        from app.utcnow import utc_now  # noqa: PLC0415

        with Session(self.engine) as session:
            session.add(
                PracticeMemberRow(
                    user_id=user_id,
                    practice_id=practice_id,
                    joined_at=utc_now(),
                    joined_via=via,
                    claimed_email=claimed_email,
                )
            )
            session.commit()

    def membership(self, user_id: str) -> tuple[str, str, str | None, datetime] | None:
        """``(practice_id, joined_via, claimed_email, joined_at)``, or None."""
        from app.db.platform_models import PracticeMemberRow as M  # noqa: PLC0415

        with Session(self.engine) as session:
            row = session.execute(
                select(M.practice_id, M.joined_via, M.claimed_email, M.joined_at).where(
                    M.user_id == user_id
                )
            ).first()
        return None if row is None else (row[0], row[1], row[2], row[3])

    def cleanup(self) -> None:
        from app.db.platform_models import (  # noqa: PLC0415
            EmailTenantMappingRow,
            PlatformUserRow,
            PracticeMemberRow,
            PracticeRow,
            UserIdentityRow,
        )

        with Session(self.engine) as session:
            session.execute(
                delete(PracticeMemberRow).where(PracticeMemberRow.user_id.in_(self.user_ids))
            )
            session.execute(delete(PracticeRow).where(PracticeRow.id.in_(self.practice_ids)))
            session.execute(
                delete(UserIdentityRow).where(UserIdentityRow.user_id.in_(self.user_ids))
            )
            session.execute(delete(PlatformUserRow).where(PlatformUserRow.id.in_(self.user_ids)))
            session.execute(
                delete(EmailTenantMappingRow).where(EmailTenantMappingRow.email.in_(self.emails))
            )
            session.commit()


@pytest.fixture
def world(engine: Engine) -> Iterator[_World]:
    made = _World(engine)
    try:
        yield made
    finally:
        made.cleanup()


@pytest.fixture(autouse=True)
def _no_redis() -> Iterator[None]:
    """Every resolution reads the database, so a cached answer cannot hide a write."""
    with patch("app.db.practice_resolution_cache.get_redis_client", return_value=None):
        yield


def _token_request(account: SimpleNamespace, email: str) -> tuple[Any, dict[str, Any]]:
    """A request whose sign-in token verified ``email``, resolved to ``account``."""
    from app.auth.providers import VerifiedIdentity  # noqa: PLC0415

    subject_id = f"sub-{uuid.uuid4().hex[:12]}"
    claims: dict[str, Any] = {"uid": subject_id, "email": email, "email_verified": True}
    identity = VerifiedIdentity(
        provider="firebase",
        subject_id=subject_id,
        email=email,
        email_verified=True,
        mfa_satisfied=True,
        claims=claims,
    )
    state = SimpleNamespace(
        verified_identity=identity,
        pablo_user_id=account.id,
        pablo_user_id_subject_key=("firebase", subject_id),
    )
    return SimpleNamespace(state=state), claims


def _tenant_context(account: SimpleNamespace, email: str) -> Any:
    """Run the real ``get_tenant_context`` for a request signed in as ``email``."""
    from app.auth.service import get_tenant_context  # noqa: PLC0415

    request, claims = _token_request(account, email)
    with (
        patch("app.db.get_db_session", return_value=MagicMock()),
        patch("app.db.arm_current_user_id"),
    ):
        return get_tenant_context(request, claims, MagicMock(), MagicMock())  # type: ignore[arg-type]


def _disagreements(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    from app.db.practice_members import MEMBERSHIP_DISAGREES_EVENT  # noqa: PLC0415

    return [r for r in caplog.records if getattr(r, "event", None) == MEMBERSHIP_DISAGREES_EVENT]


# --- the first sign-in claims, once ----------------------------------------


def test_a_first_sign_in_through_get_tenant_context_claims_once(world: _World) -> None:
    practice = world.practice("p")
    account = world.account("first")
    world.map(account.email, practice.id)

    context = _tenant_context(account, account.email)

    assert context.practice_id == practice.id
    first = world.membership(account.id)
    assert first is not None
    assert first[:3] == (practice.id, "email_claim", account.email)

    _tenant_context(account, account.email)

    assert world.membership(account.id) == first, "a second request writes nothing"


def test_the_middleware_claims_through_the_identity_row(world: _World) -> None:
    """The middleware holds only the token's identity: it finds the account
    through the identity row and may claim with the address the token verified."""
    from app.auth.providers import VerifiedIdentity  # noqa: PLC0415
    from app.db.middleware import _resolve_schema_from_request  # noqa: PLC0415

    practice = world.practice("p")
    subject_id = f"sub-{uuid.uuid4().hex[:12]}"
    account = world.account("mw", subject_id=subject_id)
    world.map(account.email, practice.id)
    identity = VerifiedIdentity(
        provider="firebase",
        subject_id=subject_id,
        email=account.email,
        email_verified=True,
        mfa_satisfied=True,
        claims={},
    )
    request = SimpleNamespace(state=SimpleNamespace(verified_identity=identity))

    schema, reason = _resolve_schema_from_request(request)  # type: ignore[arg-type]

    assert (schema, reason) == (practice.schema, "resolved")
    membership = world.membership(account.id)
    assert membership is not None
    assert membership[:2] == (practice.id, "email_claim")


def test_a_stored_email_caller_writes_no_membership(world: _World) -> None:
    """The ``_deps`` resolver passes ``users.email``, written at account creation
    and never refreshed. It resolves as before and records nothing."""
    from app.routes._deps import practice_id_or_409  # noqa: PLC0415

    practice = world.practice("p")
    account = world.account("stored")
    world.map(account.email, practice.id)

    assert practice_id_or_409(SimpleNamespace(id=account.id, email=account.email)) == practice.id  # type: ignore[arg-type]

    assert world.membership(account.id) is None


def test_a_claim_after_the_grant_is_revoked_writes_nothing(world: _World) -> None:
    """The revoke commits between the resolver's read and the claim: the claim
    checks the mapping in the same statement, so there is nothing to write."""
    from app.db.practice_members import claim  # noqa: PLC0415

    practice = world.practice("p")
    account = world.account("revoked")
    world.map(account.email, practice.id)
    world.unmap(account.email)

    assert claim(account.id, practice.id, account.email) is False
    assert world.membership(account.id) is None


def test_a_claim_with_the_grant_in_place_writes(world: _World) -> None:
    """The control for the case above: the same call, mapping intact."""
    from app.db.practice_members import claim  # noqa: PLC0415

    practice = world.practice("p")
    account = world.account("granted")
    world.map(account.email, practice.id)

    assert claim(account.id, practice.id, account.email.upper()) is True
    membership = world.membership(account.id)
    assert membership is not None
    assert membership[:3] == (practice.id, "email_claim", account.email)


def test_a_claim_names_the_practice_the_address_maps_to(world: _World) -> None:
    """An address mapped to P2 cannot claim P1, even if a caller names P1."""
    from app.db.practice_members import claim  # noqa: PLC0415

    p1, p2 = world.practice("one"), world.practice("two")
    account = world.account("elsewhere")
    world.map(account.email, p2.id)

    assert claim(account.id, p1.id, account.email) is False
    assert world.membership(account.id) is None


def test_a_claim_into_an_inactive_practice_writes_nothing(world: _World) -> None:
    from app.db.practice_members import claim  # noqa: PLC0415

    practice = world.practice("gone", active=False)
    account = world.account("late")
    world.map(account.email, practice.id)

    assert claim(account.id, practice.id, account.email) is False
    assert world.membership(account.id) is None


# --- the request that creates the account -----------------------------------


def _claim_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == "app.db.practice_members" and r.levelno >= logging.WARNING
    ]


def _in_request(session: Session, run: Any) -> Any:
    """Run ``run()`` with ``session`` published as the request session.

    In a copied context, so the request's ContextVars (the session, the schema
    ``_keep_request_off_the_template`` sets) do not outlive it.
    """

    def body() -> Any:
        from app.db import publish_request_session, restore_request_session  # noqa: PLC0415

        binding = publish_request_session(session)
        try:
            return run()
        finally:
            restore_request_session(binding)

    return contextvars.copy_context().run(body)


def test_the_request_that_creates_the_account_claims_when_it_commits(
    world: _World, engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """The account's ``users`` row is only flushed when ``get_tenant_context``
    runs, so a claim on its own session cannot reference it yet. It is written
    when the request commits, on that first request, and nothing warns."""
    practice = world.practice("p")
    with Session(engine) as session:
        account = world.account_in_request("signup", session)
        world.map(account.email, practice.id)

        with caplog.at_level(logging.DEBUG, logger="app.db.practice_members"):
            context = _in_request(session, lambda: _tenant_context(account, account.email))
            assert context.practice_id == practice.id
            assert world.membership(account.id) is None, "not before the account commits"
            session.commit()

    membership = world.membership(account.id)
    assert membership is not None
    assert membership[:3] == (practice.id, "email_claim", account.email)
    assert _claim_warnings(caplog) == []


def test_keeping_a_first_request_off_the_template_claims_when_it_commits(
    world: _World, engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """``_keep_request_off_the_template`` is the claim site the account-creating
    request passes through after ``_resolve_user``: it claims too."""
    from app.auth.service import _keep_request_off_the_template  # noqa: PLC0415
    from app.db import _TENANT_SCHEMA_KEY  # noqa: PLC0415

    practice = world.practice("p")
    with Session(engine) as session:
        account = world.account_in_request("offtpl", session)
        world.map(account.email, practice.id)

        with caplog.at_level(logging.DEBUG, logger="app.db.practice_members"):
            _in_request(session, lambda: _keep_request_off_the_template(account.id, account.email))
            assert session.info[_TENANT_SCHEMA_KEY] == practice.schema
            assert world.membership(account.id) is None, "not before the account commits"
            session.commit()

    membership = world.membership(account.id)
    assert membership is not None
    assert membership[:3] == (practice.id, "email_claim", account.email)
    assert _claim_warnings(caplog) == []


def test_a_request_that_rolls_back_claims_nothing(
    world: _World, engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """The rollback takes the account with it; a later commit on the same
    session tries the claim once more, finds no account, and stays quiet."""
    from app.db.practice_members import _DEFERRED_CLAIMS_KEY  # noqa: PLC0415

    practice = world.practice("p")
    with Session(engine) as session:
        account = world.account_in_request("rolledback", session)
        world.map(account.email, practice.id)

        with caplog.at_level(logging.DEBUG, logger="app.db.practice_members"):

            def request() -> None:
                _tenant_context(account, account.email)
                session.rollback()
                session.commit()

            _in_request(session, request)
            assert not session.info.get(_DEFERRED_CLAIMS_KEY), "the retry is not queued again"

    assert world.membership(account.id) is None
    assert _claim_warnings(caplog) == []


def test_a_claim_outside_a_request_for_an_uncommitted_account_waits_quietly(
    world: _World, engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """With no request session to wait on, the claim is left to the next
    request: no row, no WARNING. Once the account commits, the claim writes."""
    from app.db.practice_members import claim  # noqa: PLC0415

    practice = world.practice("p")
    with Session(engine) as session:
        account = world.account_in_request("norequest", session)
        world.map(account.email, practice.id)

        with caplog.at_level(logging.DEBUG, logger="app.db.practice_members"):
            assert claim(account.id, practice.id, account.email) is False
        assert world.membership(account.id) is None
        assert _claim_warnings(caplog) == []

        session.commit()

    assert claim(account.id, practice.id, account.email) is True


# --- the membership decides ----------------------------------------------------


def test_a_disagreeing_membership_wins_and_is_logged_with_ids_only(
    world: _World, caplog: pytest.LogCaptureFixture
) -> None:
    p1, p2 = world.practice("one"), world.practice("two")
    account = world.account("moved")
    world.join(account.id, p1.id, via="email_claim")
    world.map(account.email, p2.id)

    with caplog.at_level(logging.INFO):
        context = _tenant_context(account, account.email)

    assert context.practice_id == p1.id, "the membership decides, not the mapping"
    membership = world.membership(account.id)
    assert membership is not None
    assert membership[0] == p1.id, "the membership is not moved"
    records = _disagreements(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.INFO
    assert record.user_id == account.id  # type: ignore[attr-defined]
    assert record.practice_id == p1.id  # type: ignore[attr-defined]
    assert record.mapped_practice_id == p2.id  # type: ignore[attr-defined]
    local, _, domain = account.email.partition("@")
    assert local not in record.getMessage()
    for value in vars(record).values():
        assert local not in str(value)
        assert domain not in str(value)


def test_an_agreeing_membership_logs_nothing(
    world: _World, caplog: pytest.LogCaptureFixture
) -> None:
    practice = world.practice("p")
    account = world.account("agrees")
    world.join(account.id, practice.id, via="email_claim")
    world.map(account.email, practice.id)

    with caplog.at_level(logging.INFO):
        context = _tenant_context(account, account.email)

    assert context.practice_id == practice.id
    assert _disagreements(caplog) == []


# --- backfill -----------------------------------------------------------------


def test_the_backfill_gives_each_mapped_account_its_membership_once(
    world: _World, engine: Engine
) -> None:
    from app.db.practice_members import backfill  # noqa: PLC0415

    # Whatever earlier tests left mapped is given its row first, so the counts
    # below are this test's own.
    backfill(engine)

    practices = [world.practice(n) for n in ("a", "b", "c")]
    accounts = [world.account(f"bf{i}") for i in range(5)]
    for i, account in enumerate(accounts):
        world.map(account.email, practices[i % 3].id)
    # Stored with capitals, as an address typed at sign-up can be.
    shouting = accounts[4]
    with Session(engine) as session:
        from app.db.platform_models import PlatformUserRow  # noqa: PLC0415

        session.execute(
            update(PlatformUserRow)
            .where(PlatformUserRow.id == shouting.id)
            .values(email=shouting.email.upper())
        )
        session.commit()
    # Already a member somewhere else: left alone.
    settled = world.account("settled")
    world.map(settled.email, practices[0].id)
    world.join(settled.id, practices[1].id, via="invite")
    before = world.membership(settled.id)
    # Mapped to a practice that is no longer active: nothing to join.
    idle = world.account("idle")
    world.map(idle.email, world.practice("idle", active=False).id)

    report = backfill(engine)

    assert (report.inserted, report.unmembered) == (5, 0)
    for i, account in enumerate(accounts):
        membership = world.membership(account.id)
        assert membership is not None
        assert membership[:3] == (practices[i % 3].id, "backfill", account.email)
    assert world.membership(settled.id) == before
    assert world.membership(idle.id) is None

    again = backfill(engine)

    assert (again.inserted, again.unmembered) == (0, 0)
    assert world.membership(settled.id) == before


# --- deactivation ---------------------------------------------------------------


def test_deactivating_a_practice_removes_its_memberships(world: _World, engine: Engine) -> None:
    """``remove_for_practice`` runs in the deactivating transaction: its rows go
    with the deactivation, and nobody else's do."""
    from app.db.platform_models import PracticeRow  # noqa: PLC0415
    from app.db.practice_members import remove_for_practice  # noqa: PLC0415

    closing, staying = world.practice("closing"), world.practice("staying")
    leaving = [world.account(f"leaving{i}") for i in range(2)]
    for account in leaving:
        world.join(account.id, closing.id, via="email_claim")
    kept = world.account("kept")
    world.join(kept.id, staying.id, via="email_claim")

    with Session(engine) as session:
        session.execute(
            update(PracticeRow).where(PracticeRow.id == closing.id).values(is_active=False)
        )
        assert remove_for_practice(session, closing.id) == 2
        session.commit()

    assert [world.membership(a.id) for a in leaving] == [None, None]
    assert world.membership(kept.id) is not None


def test_a_rolled_back_deactivation_keeps_its_memberships(world: _World, engine: Engine) -> None:
    from app.db.platform_models import PracticeRow  # noqa: PLC0415
    from app.db.practice_members import remove_for_practice  # noqa: PLC0415

    practice = world.practice("p")
    account = world.account("stays")
    world.join(account.id, practice.id, via="email_claim")

    with Session(engine) as session:
        session.execute(
            update(PracticeRow).where(PracticeRow.id == practice.id).values(is_active=False)
        )
        remove_for_practice(session, practice.id)
        session.rollback()

    assert world.membership(account.id) is not None


# --- a writer racing a deactivation -----------------------------------------
#
# The deactivation runs on one connection and is left open; the writer runs on
# its own connection in a thread. Without a lock on the practice row the writer
# reads ``is_active`` from its snapshot, inserts at once, and the membership
# outlives the practice once both commit.


def _members_of(engine: Engine, practice_id: str) -> int:
    from app.db.platform_models import PracticeMemberRow as M  # noqa: PLC0415

    with Session(engine) as session:
        return int(
            session.execute(
                select(func.count()).select_from(M).where(M.practice_id == practice_id)
            ).scalar_one()
        )


def _waits_on(engine: Engine, blocker_pid: int, *, within: float = 10.0) -> bool:
    """Whether some backend comes to wait on a lock ``blocker_pid`` holds."""
    deadline = time.monotonic() + within
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            waiting = conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE :pid = ANY(pg_blocking_pids(pid))"
                ),
                {"pid": blocker_pid},
            ).scalar_one()
            if waiting:
                return True
            conn.rollback()
            time.sleep(0.05)
    return False


def _race_a_deactivation(
    engine: Engine, practice_id: str, write: Callable[[], object]
) -> tuple[bool, bool, list[object]]:
    """Run ``write`` in a thread while a deactivation of ``practice_id`` is open.

    Returns whether the writer waited on the deactivation, whether it was still
    running when that was checked, and what it returned (or raised).
    """
    from app.db.platform_models import PracticeRow  # noqa: PLC0415
    from app.db.practice_members import remove_for_practice  # noqa: PLC0415

    outcome: list[object] = []

    def run() -> None:
        try:
            outcome.append(write())
        except Exception as exc:  # surfaced by the caller's assertion
            outcome.append(exc)

    writer = threading.Thread(target=run, daemon=True)
    with Session(engine) as deactivation:
        try:
            pid = int(deactivation.execute(text("SELECT pg_backend_pid()")).scalar_one())
            deactivation.execute(
                update(PracticeRow).where(PracticeRow.id == practice_id).values(is_active=False)
            )
            remove_for_practice(deactivation, practice_id)
            writer.start()
            waited = _waits_on(engine, pid)
            still_running = writer.is_alive()
            deactivation.commit()
        finally:
            deactivation.rollback()
    writer.join(timeout=30)
    assert not writer.is_alive(), "the writer never finished"
    return waited, still_running, outcome


def test_a_claim_racing_a_deactivation_waits_and_writes_nothing(
    world: _World, engine: Engine
) -> None:
    from app.db.practice_members import claim  # noqa: PLC0415

    practice = world.practice("racing")
    account = world.account("racer")
    world.map(account.email, practice.id)

    waited, still_running, outcome = _race_a_deactivation(
        engine, practice.id, lambda: claim(account.id, practice.id, account.email)
    )

    assert (waited, still_running) == (True, True)
    assert outcome == [False]
    assert _members_of(engine, practice.id) == 0


def test_a_backfill_racing_a_deactivation_waits_and_writes_nothing(
    world: _World, engine: Engine
) -> None:
    from app.db.practice_members import BackfillReport, backfill  # noqa: PLC0415

    backfill(engine)
    practice = world.practice("bfracing")
    account = world.account("bfracer")
    world.map(account.email, practice.id)

    waited, still_running, outcome = _race_a_deactivation(
        engine, practice.id, lambda: backfill(engine)
    )

    assert (waited, still_running) == (True, True)
    assert outcome == [BackfillReport(inserted=0, unmembered=0)]
    assert _members_of(engine, practice.id) == 0


# --- routing by membership ------------------------------------------------------

_CACHE = "app.db.practice_resolution_cache"


@pytest.fixture
def redis() -> Iterator[FakeRedis]:
    """Redis on, as the in-memory stand-in: replaces the autouse ``_no_redis``."""
    fake = FakeRedis()
    with patch(f"{_CACHE}.get_redis_client", return_value=fake):
        yield fake


def _middleware_request(subject_id: str, email: str) -> Any:
    """What the schema middleware holds: the token's identity, nothing else."""
    from app.auth.providers import VerifiedIdentity  # noqa: PLC0415

    identity = VerifiedIdentity(
        provider="firebase",
        subject_id=subject_id,
        email=email,
        email_verified=True,
        mfa_satisfied=True,
        claims={"uid": subject_id, "email": email, "email_verified": True},
    )
    return SimpleNamespace(state=SimpleNamespace(verified_identity=identity))


def _signed_in_request(subject_id: str, email: str) -> Any:
    """One request through the middleware and then ``get_tenant_context``, as served.

    Both run against the same request object, as they do in the app: the
    middleware finds the account through its identity row and leaves what it
    learned on the request for the dependency.
    """
    from app.auth.service import get_tenant_context  # noqa: PLC0415
    from app.db.middleware import _resolve_schema_from_request  # noqa: PLC0415

    request = _middleware_request(subject_id, email)
    schema, _reason = _resolve_schema_from_request(request)
    claims = request.state.verified_identity.claims
    user_repo = MagicMock()
    user_repo.get.return_value = None
    with (
        patch("app.db.get_db_session", return_value=MagicMock()),
        patch("app.db.arm_current_user_id"),
    ):
        try:
            context = get_tenant_context(request, claims, user_repo, MagicMock())
        except HTTPException as exc:
            return schema, exc.status_code
    return schema, context.practice_id


def _resolve(account: SimpleNamespace, email: str) -> tuple[str, str] | None:
    """The auth path's resolution for a verified ``email``."""
    from app.auth.service import _resolve_practice  # noqa: PLC0415

    return _resolve_practice(account.id, email, may_claim=True)


def test_a_member_whose_address_has_no_mapping_resolves_to_their_practice(
    world: _World,
) -> None:
    """Joined other than through the mapping: the account decides, through the
    middleware's real identity lookup and through ``get_tenant_context``."""
    practice = world.practice("p")
    subject_id = f"sub-{uuid.uuid4().hex[:12]}"
    account = world.account("unmapped", subject_id=subject_id)
    world.join(account.id, practice.id, via="invite")

    assert _signed_in_request(subject_id, account.email) == (practice.schema, practice.id)


def test_a_member_of_an_inactive_practice_resolves_to_nothing(world: _World) -> None:
    """No fallback: the address maps to a live practice, and still nothing."""
    closed, live = world.practice("closed", active=False), world.practice("live")
    subject_id = f"sub-{uuid.uuid4().hex[:12]}"
    account = world.account("closed", subject_id=subject_id)
    world.join(account.id, closed.id, via="email_claim")
    world.map(account.email, live.id)

    assert _resolve(account, account.email) is None
    assert _signed_in_request(subject_id, account.email) == (None, 403)
    membership = world.membership(account.id)
    assert membership is not None
    assert membership[0] == closed.id, "nothing is claimed in its place"


def test_an_account_without_a_membership_still_resolves_by_its_mapping(
    world: _World,
) -> None:
    """The control for the cases above: the first sign-in goes where the
    address maps, and claims it."""
    practice = world.practice("p")
    subject_id = f"sub-{uuid.uuid4().hex[:12]}"
    account = world.account("newcomer", subject_id=subject_id)
    world.map(account.email, practice.id)

    assert _signed_in_request(subject_id, account.email) == (practice.schema, practice.id)
    membership = world.membership(account.id)
    assert membership is not None
    assert membership[:2] == (practice.id, "email_claim")


def test_a_signed_in_request_reads_the_membership_once_cold_and_never_warm(
    world: _World, redis: FakeRedis, caplog: pytest.LogCaptureFixture
) -> None:
    """The middleware and ``get_tenant_context`` share one answer per request:
    one membership read on a cold cache, none on a warm one, and a disagreement
    logged at most once per request."""
    from app.db import practice_members  # noqa: PLC0415

    p1, p2 = world.practice("one"), world.practice("two")
    subject_id = f"sub-{uuid.uuid4().hex[:12]}"
    account = world.account("counted", subject_id=subject_id)
    world.join(account.id, p1.id, via="email_claim")
    world.map(account.email, p2.id)

    with (
        patch.object(practice_members, "lookup", wraps=practice_members.lookup) as reads,
        caplog.at_level(logging.INFO),
    ):
        assert _signed_in_request(subject_id, account.email) == (p1.schema, p1.id)
        assert reads.call_count == 1, "cold: one read for the whole request"
        assert len(_disagreements(caplog)) == 1, "logged once for the request"

        reads.reset_mock()
        caplog.clear()
        assert _signed_in_request(subject_id, account.email) == (p1.schema, p1.id)
        assert reads.call_count == 0, "warm: answered from the cache"


def test_without_redis_a_signed_in_request_still_reads_the_membership_once(
    world: _World, caplog: pytest.LogCaptureFixture
) -> None:
    """The request keeps its own answer, so the middleware and
    ``get_tenant_context`` share it with no cache at all; the same for a
    membership in an inactive practice, which Redis never holds."""
    from app.db import practice_members  # noqa: PLC0415

    p1, p2 = world.practice("one"), world.practice("two")
    closed = world.practice("closed", active=False)
    member_sub, closed_sub = (f"sub-{uuid.uuid4().hex[:12]}" for _ in range(2))
    member = world.account("uncached", subject_id=member_sub)
    world.join(member.id, p1.id, via="email_claim")
    world.map(member.email, p2.id)
    shut_out = world.account("uncached-closed", subject_id=closed_sub)
    world.join(shut_out.id, closed.id, via="email_claim")
    world.map(shut_out.email, p2.id)

    with (
        patch.object(practice_members, "lookup", wraps=practice_members.lookup) as reads,
        caplog.at_level(logging.INFO),
    ):
        assert _signed_in_request(member_sub, member.email) == (p1.schema, p1.id)
        assert reads.call_count == 1
        assert len(_disagreements(caplog)) == 1

        reads.reset_mock()
        caplog.clear()
        assert _signed_in_request(closed_sub, shut_out.email) == (None, 403)
        assert reads.call_count == 1
        assert len(_disagreements(caplog)) == 1


def test_the_membership_is_read_before_a_cached_email_answer(
    world: _World, redis: FakeRedis
) -> None:
    """An address answer cached before the account had a membership cannot
    outlive the membership being written."""
    p1, p2 = world.practice("one"), world.practice("two")
    account = world.account("joins")
    world.map(account.email, p2.id)
    from app.auth.service import _resolve_practice_from_email  # noqa: PLC0415

    assert _resolve_practice_from_email(account.email) == (p2.id, p2.schema)
    world.join(account.id, p1.id, via="invite")

    assert _resolve(account, account.email) == (p1.id, p1.schema)


# --- revoking a grant -------------------------------------------------------------


def _grant(engine: Engine, email: str, practice_id: str) -> None:
    from app.repositories.postgres.allowlist import (  # noqa: PLC0415
        PostgresAllowlistRepository,
    )

    with Session(engine) as session:
        PostgresAllowlistRepository(session).add(email, "admin", practice_id=practice_id)
        session.commit()


def _revoke(engine: Engine, email: str) -> bool:
    from app.repositories.postgres.allowlist import (  # noqa: PLC0415
        PostgresAllowlistRepository,
    )

    with Session(engine) as session:
        removed = PostgresAllowlistRepository(session).remove(email)
        session.commit()
    return removed


def _forget_grant(engine: Engine, email: str) -> None:
    from app.db.platform_models import PlatformAllowedEmailRow  # noqa: PLC0415

    with Session(engine) as session:
        session.execute(
            delete(PlatformAllowedEmailRow).where(PlatformAllowedEmailRow.email == email)
        )
        session.commit()


def test_revoking_by_the_claimed_address_removes_a_member_whose_stored_address_differs(
    world: _World, engine: Engine
) -> None:
    """``users.email`` is written once; the account signed in later under the
    address it was granted. The revoke finds it by that address."""
    practice = world.practice("p")
    granted = f"granted-{world.label}@example.invalid"
    account = world.account("drifted", stored_email=f"old-{world.label}@example.invalid")
    _grant(engine, granted, practice.id)
    world.emails.append(granted)
    try:
        assert _resolve(account, granted) == (practice.id, practice.schema)
        membership = world.membership(account.id)
        assert membership is not None
        assert membership[:3] == (practice.id, "email_claim", granted)

        assert _revoke(engine, granted) is True

        assert world.membership(account.id) is None
        assert _resolve(account, granted) is None, "the next request resolves to nothing"
    finally:
        _forget_grant(engine, granted)


def test_revoking_a_grant_that_names_no_practice_removes_the_claimed_membership(
    world: _World, engine: Engine
) -> None:
    from app.db.platform_models import PlatformAllowedEmailRow  # noqa: PLC0415
    from app.utcnow import utc_now  # noqa: PLC0415

    practice = world.practice("p")
    account = world.account("bare")
    world.join(account.id, practice.id, via="email_claim", claimed_email=account.email)
    with Session(engine) as session:
        session.add(
            PlatformAllowedEmailRow(
                email=account.email, added_by="admin", added_at=utc_now(), practice_id=None
            )
        )
        session.commit()
    try:
        assert _revoke(engine, account.email) is True
        assert world.membership(account.id) is None
    finally:
        _forget_grant(engine, account.email)


def test_revoking_a_grant_removes_a_backfilled_membership_and_keeps_an_invited_one(
    world: _World, engine: Engine
) -> None:
    practice, elsewhere = world.practice("p"), world.practice("elsewhere")
    backfilled = world.account("backfilled")
    world.join(backfilled.id, practice.id, via="backfill", claimed_email=backfilled.email)
    invited = world.account("invited")
    world.join(invited.id, elsewhere.id, via="invite", claimed_email=invited.email)
    _grant(engine, backfilled.email, practice.id)
    _grant(engine, invited.email, practice.id)
    try:
        assert _revoke(engine, backfilled.email) is True
        assert _revoke(engine, invited.email) is True

        assert world.membership(backfilled.id) is None
        kept = world.membership(invited.id)
        assert kept is not None
        assert kept[:2] == (elsewhere.id, "invite")
    finally:
        _forget_grant(engine, backfilled.email)
        _forget_grant(engine, invited.email)


def test_revoking_after_a_regrant_elsewhere_removes_the_claimed_membership(
    world: _World, engine: Engine
) -> None:
    """Revoke, then grant: the documented way to move a member."""
    p1, p2 = world.practice("one"), world.practice("two")
    account = world.account("moving")
    _grant(engine, account.email, p1.id)
    try:
        assert _resolve(account, account.email) == (p1.id, p1.schema)
        _grant(engine, account.email, p2.id)
        assert _resolve(account, account.email) == (p1.id, p1.schema), "a re-grant moves nobody"

        assert _revoke(engine, account.email) is True
        assert world.membership(account.id) is None
        _grant(engine, account.email, p2.id)

        assert _resolve(account, account.email) == (p2.id, p2.schema)
    finally:
        _forget_grant(engine, account.email)


# --- with Redis on, a change is honoured on the next request --------------------


def test_moving_a_member_is_honoured_on_the_next_request(
    world: _World, engine: Engine, redis: FakeRedis
) -> None:
    from app.db.platform_models import PracticeMemberRow  # noqa: PLC0415
    from app.db.practice_resolution_cache import _USER_PREFIX  # noqa: PLC0415

    p1, p2 = world.practice("one"), world.practice("two")
    account = world.account("cached-move")
    world.join(account.id, p1.id)
    assert _resolve(account, account.email) == (p1.id, p1.schema)
    assert redis.keys_with_prefix(_USER_PREFIX), "the membership answer is cached"

    with Session(engine) as session:
        row = session.get(PracticeMemberRow, account.id)
        assert row is not None
        row.practice_id = p2.id
        session.commit()

    assert _resolve(account, account.email) == (p2.id, p2.schema)


def test_removing_a_membership_is_honoured_on_the_next_request(
    world: _World, engine: Engine, redis: FakeRedis
) -> None:
    from app.db.platform_models import PracticeMemberRow  # noqa: PLC0415
    from app.db.practice_resolution_cache import _USER_PREFIX  # noqa: PLC0415

    practice = world.practice("p")
    account = world.account("cached-remove")
    world.join(account.id, practice.id)
    assert _resolve(account, account.email) == (practice.id, practice.schema)

    with Session(engine) as session:
        row = session.get(PracticeMemberRow, account.id)
        assert row is not None
        session.delete(row)
        session.commit()

    assert redis.keys_with_prefix(_USER_PREFIX) == []
    assert _resolve(account, account.email) is None


def test_a_bulk_membership_update_is_honoured_on_the_next_request(
    world: _World, engine: Engine, redis: FakeRedis
) -> None:
    from app.db.platform_models import PracticeMemberRow  # noqa: PLC0415

    p1, p2 = world.practice("one"), world.practice("two")
    account = world.account("cached-bulk")
    world.join(account.id, p1.id)
    assert _resolve(account, account.email) == (p1.id, p1.schema)

    with Session(engine) as session:
        session.execute(
            update(PracticeMemberRow)
            .where(PracticeMemberRow.practice_id == p1.id)
            .values(practice_id=p2.id)
        )
        session.commit()

    assert _resolve(account, account.email) == (p2.id, p2.schema)


def test_revoking_a_grant_is_honoured_past_a_cached_answer(
    world: _World, engine: Engine, redis: FakeRedis
) -> None:
    practice = world.practice("p")
    account = world.account("cached-revoke")
    _grant(engine, account.email, practice.id)
    try:
        assert _resolve(account, account.email) == (practice.id, practice.schema)
        assert _resolve(account, account.email) == (practice.id, practice.schema)

        _revoke(engine, account.email)

        assert _resolve(account, account.email) is None
    finally:
        _forget_grant(engine, account.email)


def test_deleting_an_account_drops_its_cached_answer(
    world: _World, engine: Engine, redis: FakeRedis
) -> None:
    """The database removes the membership by cascade, which no flush shows;
    deleting the account's row drops its entry itself."""
    from app.db.platform_models import PlatformUserRow  # noqa: PLC0415
    from app.db.practice_resolution_cache import _user_key  # noqa: PLC0415

    practice = world.practice("p")
    account = world.account("deleted")
    world.join(account.id, practice.id)
    assert _resolve(account, account.email) == (practice.id, practice.schema)
    assert redis.get(_user_key(account.id)) is not None

    with Session(engine) as session:
        row = session.get(PlatformUserRow, account.id)
        assert row is not None
        session.delete(row)
        session.commit()

    assert redis.get(_user_key(account.id)) is None
    assert world.membership(account.id) is None, "the cascade took the membership"
