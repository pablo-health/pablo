# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Each account's practice membership is recorded, against Postgres; routing is not.

A signed-in request still resolves its practice through the email mapping.
Beside that answer, a request whose address the sign-in token verified records
the account's membership (``platform.practice_members``) the first time, and
afterwards compares the two and logs a disagreement with ids only. Callers that
pass the address stored on the account never write.

Nothing on the resolution path is patched except Redis (off) and the request
session's RLS arm, which needs a live request. The rows are the ones the code
reads and writes.

Run: ``make test-integration``.
"""

from __future__ import annotations

import contextvars
import logging
import os
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator
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

    def join(self, user_id: str, practice_id: str, *, via: str = "invite") -> None:
        from app.db.platform_models import PracticeMemberRow  # noqa: PLC0415
        from app.utcnow import utc_now  # noqa: PLC0415

        with Session(self.engine) as session:
            session.add(
                PracticeMemberRow(
                    user_id=user_id, practice_id=practice_id, joined_at=utc_now(), joined_via=via
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


# --- shadow mode: routing unchanged, disagreement logged ----------------------


def test_a_disagreeing_membership_does_not_route_and_is_logged_with_ids_only(
    world: _World, caplog: pytest.LogCaptureFixture
) -> None:
    p1, p2 = world.practice("one"), world.practice("two")
    account = world.account("moved")
    world.join(account.id, p1.id, via="email_claim")
    world.map(account.email, p2.id)

    with caplog.at_level(logging.INFO):
        context = _tenant_context(account, account.email)

    assert context.practice_id == p2.id, "the email mapping still decides"
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
