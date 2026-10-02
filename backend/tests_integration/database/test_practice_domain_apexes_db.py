# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The registrable domains a practice's hosts sit under, against real PostgreSQL.

Proves what the in-memory repository in ``tests/test_practice_domains_routes.py``
can only imitate: the domain primary key keeps a domain to one practice, the
CHECK refuses an email identity state that does not exist, the DKIM tokens
round-trip through JSONB, and a host's certificate authorisation value is
stored. Then the service on the real repository: a host under another
practice's domain is refused and writes nothing, and a DNS check records
ownership. Last, the revision can be taken down and brought up twice.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.api_errors import ConflictError, ForbiddenError
from app.models.practice_domain import PracticeDomain, PracticeDomainApex
from app.repositories.postgres.practice_domain import PostgresPracticeDomainRepository
from app.repositories.practice_domain import DomainTakenError
from app.services.practice_domain_allowance import DomainAllowance
from app.services.practice_domain_service import PracticeDomainService
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic_platform"
    / "versions"
    / "b3e91d5a7c24_practice_domain_apexes.py"
)
TARGET = "sites.example.net"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def repo(session: Session) -> PostgresPracticeDomainRepository:
    return PostgresPracticeDomainRepository(session)


def _practice() -> str:
    return f"practice-apexes-{uuid.uuid4().hex[:8]}"


def _apex_name() -> str:
    return f"{uuid.uuid4().hex[:10]}.example"


def _apex(apex: str, practice_id: str, **overrides: object) -> PracticeDomainApex:
    row = PracticeDomainApex(
        apex=apex,
        practice_id=practice_id,
        verify_token=uuid.uuid4().hex,
        created_at=datetime.now(UTC),
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def test_a_domain_belongs_to_one_practice(repo: PostgresPracticeDomainRepository) -> None:
    apex, ours, theirs = _apex_name(), _practice(), _practice()
    repo.add_apex(_apex(apex, theirs))

    with pytest.raises(DomainTakenError):
        repo.add_apex(_apex(apex, ours))

    stored = repo.get_apex(apex)
    assert stored is not None
    assert stored.practice_id == theirs
    assert repo.list_apexes_for_practice(ours) == []


def test_identity_state_and_dkim_tokens_round_trip(
    repo: PostgresPracticeDomainRepository,
) -> None:
    apex, practice = _apex_name(), _practice()
    repo.add_apex(
        _apex(apex, practice, email_identity_status="pending", email_dkim_tokens=["a", "b", "c"])
    )

    stored = repo.get_apex(apex)
    assert stored is not None
    assert stored.email_identity_status == "pending"
    assert stored.email_dkim_tokens == ["a", "b", "c"]
    assert [a.apex for a in repo.list_apexes_for_practice(practice)] == [apex]


def test_an_unknown_identity_state_is_refused(session: Session) -> None:
    with pytest.raises(IntegrityError, match="email_identity_status_check"):
        session.execute(
            text(
                "INSERT INTO platform.practice_domain_apexes "
                "(apex, practice_id, verify_token, email_identity_status, created_at) "
                "VALUES (:a, 'p', 't', 'sending', now())"
            ),
            {"a": _apex_name()},
        )


def test_verification_and_removal_are_scoped_to_the_practice(
    repo: PostgresPracticeDomainRepository,
) -> None:
    apex, ours, theirs = _apex_name(), _practice(), _practice()
    repo.add_apex(_apex(apex, theirs))
    now = datetime.now(UTC)

    repo.mark_apex_verified(apex, ours, now)
    assert repo.remove_apex(apex, ours) is False
    assert repo.get_apex(apex).verified_at is None  # type: ignore[union-attr]  # added above

    repo.mark_apex_verified(apex, theirs, now)
    assert repo.get_apex(apex).verified_at == now  # type: ignore[union-attr]  # added above
    assert repo.remove_apex(apex, theirs) is True
    assert repo.get_apex(apex) is None


def test_a_hosts_certificate_authorisation_value_is_stored(
    repo: PostgresPracticeDomainRepository,
) -> None:
    host = f"portal.{_apex_name()}"
    repo.add(
        PracticeDomain(
            domain=host,
            practice_id=_practice(),
            purpose="portal",
            kind="vanity",
            status="pending",
            is_primary=False,
            created_at=datetime.now(UTC),
            cert_auth_value="test-auth.5",
        )
    )
    assert repo.get(host).cert_auth_value == "test-auth.5"  # type: ignore[union-attr]  # added above


def test_a_host_under_another_practices_domain_writes_nothing(
    repo: PostgresPracticeDomainRepository,
) -> None:
    service = PracticeDomainService(repo, cname_target=TARGET)
    apex, ours, theirs = _apex_name(), _practice(), _practice()
    service.add(theirs, f"portal.{apex}", "portal")

    with pytest.raises(ConflictError):
        service.add(ours, f"book.{apex}", "portal")

    assert repo.get(f"book.{apex}") is None
    assert repo.get_apex(apex).practice_id == theirs  # type: ignore[union-attr]  # added above


def test_a_check_records_ownership_and_leaves_the_host_alone(
    repo: PostgresPracticeDomainRepository,
) -> None:
    service = PracticeDomainService(repo, cname_target=TARGET)
    apex, practice = _apex_name(), _practice()
    service.add(practice, f"portal.{apex}", "portal")
    token = repo.get_apex(apex).verify_token  # type: ignore[union-attr]  # added above
    table = {
        (f"_pablo-verify.{apex}", "TXT"): [f"pablo-verify={token}"],
        (f"portal.{apex}", "CNAME"): [TARGET],
    }

    responses, confirmed, _stuck = service.check(
        practice, lambda name, rdtype: table.get((name, rdtype), [])
    )

    assert confirmed == [apex]
    assert {r.check for r in responses[0].dns_records} == {"ok"}
    assert repo.get_apex(apex).verified_at is not None  # type: ignore[union-attr]  # added above
    assert repo.get(f"portal.{apex}").status == "pending"  # type: ignore[union-attr]  # added above


def test_a_domain_past_the_allowance_writes_nothing(
    repo: PostgresPracticeDomainRepository,
) -> None:
    service = PracticeDomainService(
        repo,
        cname_target=TARGET,
        allowance=lambda _practice_id: DomainAllowance(limit=1, message="Limit reached."),
    )
    first, second, practice = _apex_name(), _apex_name(), _practice()
    service.add(practice, first, "site")
    service.add(practice, f"portal.{first}", "portal")

    with pytest.raises(ForbiddenError) as refused:
        service.add(practice, second, "site")

    assert (refused.value.code, refused.value.message) == ("DOMAIN_LIMIT", "Limit reached.")
    assert repo.get(second) is None
    assert repo.get(f"www.{second}") is None
    assert repo.get_apex(second) is None
    assert [a.apex for a in repo.list_apexes_for_practice(practice)] == [first]


def _migration(direction: str, engine: Engine) -> None:
    spec = importlib.util.spec_from_file_location("practice_domain_apexes_revision", _MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(module, direction)()


def test_the_revision_goes_down_and_comes_up_twice(engine: Engine) -> None:
    def shape() -> tuple[bool, bool, set[str]]:
        with engine.begin() as conn:
            table = conn.execute(
                text("SELECT to_regclass('platform.practice_domain_apexes') IS NOT NULL")
            ).scalar_one()
            column = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = 'platform' AND table_name = 'practice_domains' "
                    "AND column_name = 'cert_auth_value'"
                )
            ).scalar_one()
            indexes = set(
                conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE schemaname = 'platform' "
                        "AND tablename = 'practice_domain_apexes'"
                    )
                ).scalars()
            )
        return bool(table), bool(column), indexes

    _migration("downgrade", engine)
    try:
        assert shape() == (False, False, set())
    finally:
        _migration("upgrade", engine)
    _migration("upgrade", engine)

    table, column, indexes = shape()
    assert table
    assert column
    assert {"practice_domain_apexes_pkey", "ix_practice_domain_apexes_practice_id"} <= indexes
