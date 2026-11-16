# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A signed-in account resolves its practice from its membership, against Postgres.

The email mapping decides an account's practice once, on its first sign-in,
and that sign-in writes ``platform.practice_members``. From then on the
membership decides: a changed address does not move the account, and an
address that maps somewhere else cannot override it.

Nothing on the resolution path is patched here. The rows are the ones the
resolver reads, and the middleware case goes through the real identity lookup.
The cache cases use the in-memory Redis the cache's own suite uses.

Run: ``make test-integration``.
"""

from __future__ import annotations

import logging
import os
import types
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from tests.practice_resolution_fakes import FakeRedis

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_CACHE = "app.db.practice_resolution_cache"
_USER_PREFIX = "practice-resolution:v1:user:"


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

    def account(self, name: str, *, subject_id: str | None = None) -> SimpleNamespace:
        """A platform user, and its identity row when ``subject_id`` is given."""
        from app.db.platform_models import PlatformUserRow, UserIdentityRow  # noqa: PLC0415
        from app.utcnow import utc_now  # noqa: PLC0415

        user_id = str(uuid.uuid4())
        email = f"{name}-{self.label}@example.invalid"
        with Session(self.engine) as session:
            session.add(PlatformUserRow(id=user_id, email=email, name=name, created_at=utc_now()))
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

    def membership(self, user_id: str) -> tuple[str, str] | None:
        from app.db.platform_models import PracticeMemberRow  # noqa: PLC0415

        with Session(self.engine) as session:
            row = session.execute(
                select(PracticeMemberRow.practice_id, PracticeMemberRow.joined_via).where(
                    PracticeMemberRow.user_id == user_id
                )
            ).first()
        return None if row is None else (row[0], row[1])

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
            session.execute(
                delete(UserIdentityRow).where(UserIdentityRow.user_id.in_(self.user_ids))
            )
            session.execute(delete(PlatformUserRow).where(PlatformUserRow.id.in_(self.user_ids)))
            session.execute(
                delete(EmailTenantMappingRow).where(EmailTenantMappingRow.email.in_(self.emails))
            )
            session.execute(delete(PracticeRow).where(PracticeRow.id.in_(self.practice_ids)))
            session.commit()


@pytest.fixture
def world(engine: Engine) -> Iterator[_World]:
    made = _World(engine)
    try:
        yield made
    finally:
        made.cleanup()


@pytest.fixture
def no_redis() -> Iterator[None]:
    with patch(f"{_CACHE}.get_redis_client", return_value=None):
        yield


@pytest.fixture
def redis() -> Iterator[FakeRedis]:
    fake = FakeRedis()
    with patch(f"{_CACHE}.get_redis_client", return_value=fake):
        yield fake


def _resolve(user_id: str | None, email: str | None) -> tuple[str, str] | None:
    from app.auth.service import _resolve_practice  # noqa: PLC0415

    return _resolve_practice(user_id, email)


# ---------------------------------------------------------------------------
# AC1: membership resolves without a mapping
# ---------------------------------------------------------------------------


def test_a_member_whose_address_has_no_mapping_resolves_to_their_practice(
    world: _World, no_redis: None
) -> None:
    practice = world.practice("a")
    account = world.account("member")
    world.join(account.id, practice.id)

    assert _resolve(account.id, account.email) == (practice.id, practice.schema)


def test_the_middleware_resolves_a_member_through_the_identity_row(
    world: _World, no_redis: None
) -> None:
    """The request path holds only the token's identity: it finds the account
    from the identity row and resolves by membership, address unmapped."""
    from app.db.middleware import _resolve_schema_from_request  # noqa: PLC0415

    practice = world.practice("a")
    subject = f"fb{uuid.uuid4().hex[:26]}"
    account = world.account("member", subject_id=subject)
    world.join(account.id, practice.id)
    identity = types.SimpleNamespace(
        provider="firebase", subject_id=subject, email=account.email, email_verified=True
    )
    request = types.SimpleNamespace(state=types.SimpleNamespace(verified_identity=identity))

    schema, reason = _resolve_schema_from_request(request)  # type: ignore[arg-type]

    assert (schema, reason) == (practice.schema, "resolved")
    # Remembered for the auth dependencies, which look the account up again.
    assert request.state.pablo_user_id == account.id


def test_a_membership_in_an_inactive_practice_resolves_to_nothing(
    world: _World, no_redis: None
) -> None:
    """As an inactive practice always has, and the mapping is no way around it."""
    closed = world.practice("closed", active=False)
    other = world.practice("other")
    account = world.account("member")
    world.join(account.id, closed.id)
    world.map(account.email, other.id)

    assert _resolve(account.id, account.email) is None


# ---------------------------------------------------------------------------
# AC2: the first claim writes the membership, and only for a resolved account
# ---------------------------------------------------------------------------


def test_a_first_sign_in_through_the_mapping_writes_the_membership(
    world: _World, no_redis: None
) -> None:
    practice = world.practice("a")
    account = world.account("first")
    world.map(account.email, practice.id)

    assert _resolve(account.id, account.email) == (practice.id, practice.schema)
    assert world.membership(account.id) == (practice.id, "email_claim")


def test_an_unresolved_account_resolves_by_mapping_and_writes_nothing(
    world: _World, no_redis: None
) -> None:
    """Before the identity row exists a caller holds only the provider's
    subject id. Nothing is written for it, under either spelling."""
    from app.db.platform_models import PracticeMemberRow  # noqa: PLC0415

    practice = world.practice("a")
    account = world.account("early")
    world.map(account.email, practice.id)

    assert _resolve(None, account.email) == (practice.id, practice.schema)
    assert _resolve("fbSubjectIdNotAUuid0000000000", account.email) == (
        practice.id,
        practice.schema,
    )
    with Session(world.engine) as session:
        written = session.execute(
            select(PracticeMemberRow.user_id).where(PracticeMemberRow.practice_id == practice.id)
        ).all()
    assert written == []


def test_the_owner_is_still_recorded_on_the_first_claim(world: _World, no_redis: None) -> None:
    """``record_owner_on_sign_in`` keeps working beside the claim: the practice
    is registered under the address, and the account that claims it owns it."""
    from app.db.platform_models import PracticeRow  # noqa: PLC0415
    from app.db.practice_owner import record_owner_on_sign_in  # noqa: PLC0415

    practice = world.practice("a")
    account = world.account("owner")
    world.map(account.email, practice.id)
    with Session(world.engine) as session:
        session.execute(
            update(PracticeRow)
            .where(PracticeRow.id == practice.id)
            .values(owner_email=account.email)
        )
        session.commit()

    resolved = _resolve(account.id, account.email)
    assert resolved is not None
    assert record_owner_on_sign_in(resolved[0], account.email, account.id) is True
    assert world.membership(account.id) == (practice.id, "email_claim")


def test_a_changed_address_does_not_move_a_member(world: _World, no_redis: None) -> None:
    """The case this exists for: after the first claim, the account decides."""
    first = world.practice("first")
    later = world.practice("later")
    account = world.account("mover")
    world.map(account.email, first.id)
    assert _resolve(account.id, account.email) == (first.id, first.schema)

    new_address = f"renamed-{world.label}@example.invalid"
    world.map(new_address, later.id)

    assert _resolve(account.id, new_address) == (first.id, first.schema)


# ---------------------------------------------------------------------------
# AC3: membership wins a disagreement, and the event carries ids only
# ---------------------------------------------------------------------------


def test_membership_wins_a_disagreement_and_logs_ids_only(
    world: _World, no_redis: None, caplog: pytest.LogCaptureFixture
) -> None:
    from app.db.practice_members import MEMBERSHIP_DISAGREES_EVENT  # noqa: PLC0415

    member_of = world.practice("member")
    mapped_to = world.practice("mapped")
    account = world.account("torn")
    world.join(account.id, member_of.id)
    world.map(account.email, mapped_to.id)

    with caplog.at_level(logging.INFO, logger="app.auth.service"):
        assert _resolve(account.id, account.email) == (member_of.id, member_of.schema)

    events = [r for r in caplog.records if getattr(r, "event", None) == MEMBERSHIP_DISAGREES_EVENT]
    assert len(events) == 1
    record: Any = events[0]
    assert record.user_id == account.id
    assert record.practice_id == member_of.id
    assert record.mapped_practice_id == mapped_to.id
    assert account.email not in record.getMessage()
    assert all(account.email not in str(value) for value in vars(record).values())
    # The account keeps its membership; nothing was re-claimed from the mapping.
    assert world.membership(account.id) == (member_of.id, "invite")


def test_agreement_logs_nothing(
    world: _World, no_redis: None, caplog: pytest.LogCaptureFixture
) -> None:
    from app.db.practice_members import MEMBERSHIP_DISAGREES_EVENT  # noqa: PLC0415

    practice = world.practice("a")
    account = world.account("calm")
    world.map(account.email, practice.id)
    world.join(account.id, practice.id)

    with caplog.at_level(logging.INFO, logger="app.auth.service"):
        assert _resolve(account.id, account.email) == (practice.id, practice.schema)

    assert not [
        r for r in caplog.records if getattr(r, "event", None) == MEMBERSHIP_DISAGREES_EVENT
    ]


# ---------------------------------------------------------------------------
# AC4: the backfill
# ---------------------------------------------------------------------------


def test_the_backfill_gives_each_mapped_account_its_membership_once(
    world: _World, engine: Engine
) -> None:
    """3 practices, 5 mapped accounts: 5 rows, and a second run changes nothing."""
    from app.db.practice_members import backfill  # noqa: PLC0415

    practices = [world.practice(name) for name in ("one", "two", "three")]
    accounts = [world.account(f"mapped{i}") for i in range(5)]
    for i, account in enumerate(accounts):
        world.map(account.email, practices[i % 3].id)
    # An account with no mapping gets nothing.
    unmapped = world.account("unmapped")

    first = backfill(engine)
    after_first = {a.id: world.membership(a.id) for a in accounts}
    second = backfill(engine)
    after_second = {a.id: world.membership(a.id) for a in accounts}

    assert after_first == {a.id: (practices[i % 3].id, "backfill") for i, a in enumerate(accounts)}
    assert first.inserted >= 5  # other suites' accounts may be in the same database
    assert first.unmembered == 0
    assert second.inserted == 0
    assert second.unmembered == 0
    assert after_second == after_first
    assert world.membership(unmapped.id) is None


def test_the_backfill_leaves_an_existing_membership_alone(world: _World, engine: Engine) -> None:
    from app.db.practice_members import backfill  # noqa: PLC0415

    member_of = world.practice("member")
    mapped_to = world.practice("mapped")
    account = world.account("kept")
    world.join(account.id, member_of.id)
    world.map(account.email, mapped_to.id)

    backfill(engine)

    assert world.membership(account.id) == (member_of.id, "invite")


# ---------------------------------------------------------------------------
# AC7: with Redis on, a membership change is honoured on the next request
# ---------------------------------------------------------------------------


@contextmanager
def _orm(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session
        session.commit()


def test_moving_a_member_is_honoured_on_the_next_resolution(
    world: _World, engine: Engine, redis: FakeRedis
) -> None:
    from app.db.platform_models import PracticeMemberRow  # noqa: PLC0415

    before = world.practice("before")
    after = world.practice("after")
    account = world.account("moved")
    world.join(account.id, before.id)

    assert _resolve(account.id, account.email) == (before.id, before.schema)
    assert redis.keys_with_prefix(_USER_PREFIX), "the membership answer is cached"
    assert _resolve(account.id, account.email) == (before.id, before.schema)

    with _orm(engine) as session:
        row = session.get(PracticeMemberRow, account.id)
        assert row is not None
        row.practice_id = after.id

    assert _resolve(account.id, account.email) == (after.id, after.schema)


def test_removing_a_membership_is_honoured_on_the_next_resolution(
    world: _World, engine: Engine, redis: FakeRedis
) -> None:
    """With no mapping to fall back on, the account resolves to nothing at once."""
    from app.db.platform_models import PracticeMemberRow  # noqa: PLC0415

    practice = world.practice("a")
    account = world.account("removed")
    world.join(account.id, practice.id)
    assert _resolve(account.id, account.email) == (practice.id, practice.schema)

    with _orm(engine) as session:
        row = session.get(PracticeMemberRow, account.id)
        assert row is not None
        session.delete(row)

    assert _resolve(account.id, account.email) is None
    assert redis.keys_with_prefix(_USER_PREFIX) == []


def test_a_new_membership_overrides_a_cached_email_answer(
    world: _World, engine: Engine, redis: FakeRedis
) -> None:
    """An email-keyed answer in the cache never overrides membership."""
    mapped_to = world.practice("mapped")
    member_of = world.practice("member")
    account = world.account("invited")
    world.map(account.email, mapped_to.id)
    # The address's answer is cached, as an earlier request would have left it.
    from app.auth.service import _resolve_practice_from_email  # noqa: PLC0415

    assert _resolve_practice_from_email(account.email) == (mapped_to.id, mapped_to.schema)
    world.join(account.id, member_of.id)

    assert _resolve(account.id, account.email) == (member_of.id, member_of.schema)


def test_a_bulk_membership_update_drops_every_cached_answer(
    world: _World, engine: Engine, redis: FakeRedis
) -> None:
    from app.db.platform_models import PracticeMemberRow  # noqa: PLC0415

    before = world.practice("before")
    after = world.practice("after")
    account = world.account("bulk")
    world.join(account.id, before.id)
    assert _resolve(account.id, account.email) == (before.id, before.schema)

    with _orm(engine) as session:
        session.execute(
            update(PracticeMemberRow)
            .where(PracticeMemberRow.practice_id == before.id)
            .values(practice_id=after.id)
        )

    assert _resolve(account.id, account.email) == (after.id, after.schema)
