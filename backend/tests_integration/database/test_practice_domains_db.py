# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's own domains against real PostgreSQL.

Proves what the in-memory repository in ``tests/test_practice_domains_routes.py``
can only imitate: the hostname primary key keeps a host to one practice, the
CHECK refuses a purpose that does not exist, the partial unique index allows one
primary per practice and purpose, and choosing a new primary moves it in one
transaction without ever tripping that index. Also that the revision brings an
earlier table — one without purpose, primary or verification — up to shape.
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
from app.models.practice_domain import PracticeDomain
from app.repositories.postgres.practice_domain import PostgresPracticeDomainRepository
from app.repositories.practice_domain import DomainTakenError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models.practice_domain import DomainPurpose
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic_platform"
    / "versions"
    / "f8c2a61d4e97_practice_domains.py"
)


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
    return f"practice-domains-{uuid.uuid4().hex[:8]}"


def _host(prefix: str = "portal") -> str:
    return f"{prefix}.{uuid.uuid4().hex[:10]}.example"


def _domain(host: str, practice_id: str, *, purpose: DomainPurpose = "portal") -> PracticeDomain:
    return PracticeDomain(
        domain=host,
        practice_id=practice_id,
        purpose=purpose,
        kind="vanity",
        status="active",
        is_primary=False,
        created_at=datetime.now(UTC),
    )


def test_a_host_belongs_to_one_practice(repo: PostgresPracticeDomainRepository) -> None:
    host = _host()
    repo.add(_domain(host, _practice()))

    with pytest.raises(DomainTakenError):
        repo.add(_domain(host, _practice()))
    # The savepoint undid only the failed insert.
    assert repo.get(host) is not None


def test_purpose_must_be_portal_or_site(session: Session) -> None:
    with pytest.raises(IntegrityError, match="practice_domains_purpose_check"):
        session.execute(
            text(
                "INSERT INTO platform.practice_domains "
                "(domain, practice_id, purpose, kind, created_at) "
                "VALUES (:d, :p, 'other', 'vanity', now())"
            ),
            {"d": _host(), "p": _practice()},
        )


def test_a_row_written_without_the_new_columns_is_a_pending_website(session: Session) -> None:
    host = _host("www")
    session.execute(
        text(
            "INSERT INTO platform.practice_domains (domain, practice_id, kind, created_at) "
            "VALUES (:d, :p, 'vanity', now())"
        ),
        {"d": host, "p": _practice()},
    )
    row = session.execute(
        text(
            "SELECT purpose, status, is_primary, verified_at "
            "FROM platform.practice_domains WHERE domain = :d"
        ),
        {"d": host},
    ).one()
    assert tuple(row) == ("site", "pending", False, None)


def test_the_index_allows_one_primary_per_practice_and_purpose(session: Session) -> None:
    practice = _practice()
    insert = text(
        "INSERT INTO platform.practice_domains "
        "(domain, practice_id, purpose, kind, status, is_primary, created_at) "
        "VALUES (:d, :p, :purpose, 'vanity', 'active', true, now())"
    )
    session.execute(insert, {"d": _host(), "p": practice, "purpose": "portal"})
    # Another purpose, and another practice, each have their own primary.
    session.execute(insert, {"d": _host("www"), "p": practice, "purpose": "site"})
    session.execute(insert, {"d": _host(), "p": _practice(), "purpose": "portal"})

    with pytest.raises(IntegrityError, match="uq_practice_domains_primary"):
        session.execute(insert, {"d": _host(), "p": practice, "purpose": "portal"})


def test_choosing_a_primary_moves_it(repo: PostgresPracticeDomainRepository) -> None:
    practice = _practice()
    first, second, site = _host(), _host(), _host("www")
    repo.add(_domain(first, practice))
    repo.add(_domain(second, practice))
    repo.add(_domain(site, practice, purpose="site"))

    repo.set_primary(first, practice, "portal")
    repo.set_primary(site, practice, "site")
    repo.set_primary(second, practice, "portal")

    primaries = {d.domain for d in repo.list_for_practice(practice) if d.is_primary}
    assert primaries == {second, site}


def test_writes_are_scoped_to_the_practice(repo: PostgresPracticeDomainRepository) -> None:
    ours, theirs = _practice(), _practice()
    host = _host()
    repo.add(_domain(host, theirs))

    assert repo.remove(host, ours) is False
    repo.set_primary(host, ours, "portal")

    kept = repo.get(host)
    assert kept is not None
    assert kept.practice_id == theirs
    assert kept.is_primary is False
    assert repo.list_for_practice(ours) == []


def _migration(direction: str, engine: Engine) -> None:
    spec = importlib.util.spec_from_file_location("practice_domains_revision", _MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(module, direction)()


def test_the_revision_brings_an_earlier_table_up_and_can_run_twice(engine: Engine) -> None:
    host = _host("www")
    _migration("downgrade", engine)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform.practice_domains "
                    "(domain, practice_id, kind, status, created_at) "
                    "VALUES (:d, :p, 'vanity', 'active', now())"
                ),
                {"d": host, "p": _practice()},
            )
    finally:
        _migration("upgrade", engine)
    _migration("upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT purpose, is_primary, verified_at "
                "FROM platform.practice_domains WHERE domain = :d"
            ),
            {"d": host},
        ).one()
        indexes = set(
            conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = 'platform' AND tablename = 'practice_domains'"
                )
            ).scalars()
        )
        purpose_check = conn.execute(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conname = 'practice_domains_purpose_check'"
            )
        ).scalar_one()
    assert tuple(row) == ("site", False, None)
    assert {"uq_practice_domains_primary", "ix_practice_domains_practice_id"} <= indexes
    assert purpose_check == 1
