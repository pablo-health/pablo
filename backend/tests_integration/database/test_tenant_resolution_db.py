# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice resolution against a real database, with nothing patched out.

The middleware resolves a practice schema from the request identity. Several
integration suites replace ``_resolve_schema_from_request`` with a lambda so
they can get on with testing something else — which is reasonable for those
suites, but it means none of them exercises resolution itself. These do, by
seeding the platform rows resolution actually reads and then calling it.

Both directions matter. Resolving a seeded identity proves the happy path; an
identity with no mapping proves the other one, and that is the case that
otherwise reaches a route and queries the default schema, where the practice
tables are not.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine

if TYPE_CHECKING:
    from collections.abc import Iterator

_db_url = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _db_url, reason="TEST_DATABASE_URL not set — integration lane only"
)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@contextlib.contextmanager
def _seeded_practice(engine: Engine, email: str) -> Iterator[str]:
    """Provision a practice schema and map ``email`` to it, then clean up.

    Deliberately the same rows the resolver reads — a practice, a platform
    user, and the email mapping — so this covers the real lookup rather than a
    stand-in for it.
    """
    from app.db.platform_models import (  # noqa: PLC0415
        EmailTenantMappingRow,
        PlatformUserRow,
        PracticeRow,
    )
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    practice_id = str(uuid.uuid4())
    schema = f"practice_{practice_id.replace('-', '_')}"
    now = datetime.now(UTC)
    create_practice_schema(engine, schema)

    with OrmSession(engine) as session:
        session.add(
            PracticeRow(id=practice_id, name="Resolution Test", schema_name=schema, created_at=now)
        )
        session.add(PlatformUserRow(id=str(uuid.uuid4()), email=email, created_at=now))
        session.add(
            EmailTenantMappingRow(
                email=email, tenant_id=practice_id, practice_id=practice_id, created_at=now
            )
        )
        session.commit()

    try:
        yield schema
    finally:
        with OrmSession(engine) as session:
            session.query(EmailTenantMappingRow).filter_by(email=email).delete()
            session.query(PlatformUserRow).filter_by(email=email).delete()
            session.query(PracticeRow).filter_by(id=practice_id).delete()
            session.commit()


def test_a_mapped_identity_resolves_to_its_practice_schema(engine: Engine) -> None:
    """The happy path, through the real lookup rather than a stand-in."""
    from app.auth.service import _resolve_practice_from_email  # noqa: PLC0415

    email = f"resolves-{uuid.uuid4().hex[:8]}@example.invalid"
    with _seeded_practice(engine, email) as schema:
        practice = _resolve_practice_from_email(email)

    assert practice is not None, "a seeded mapping must resolve"
    assert practice[1] == schema


def test_an_unmapped_identity_resolves_to_nothing(engine: Engine) -> None:
    """The case that otherwise reaches a route and queries the default schema.

    Nothing is seeded for this address, so resolution finds no practice. The
    middleware then falls back to the default schema — where the practice
    tables are not — which is why the fallback now reports a reason.
    """
    from app.auth.service import _resolve_practice_from_email  # noqa: PLC0415

    unmapped = f"unmapped-{uuid.uuid4().hex[:8]}@example.invalid"

    assert _resolve_practice_from_email(unmapped) is None


def test_the_middleware_reports_why_an_unmapped_identity_is_unresolved() -> None:
    """Ties the database fact above to what the request path records.

    Needs no database of its own — the point is that the reason the middleware
    reports for "no mapping" is the same case the query above produces.
    """
    import types  # noqa: PLC0415

    from app.db.middleware import (  # noqa: PLC0415
        UNRESOLVED_NO_MAPPING,
        _resolve_schema_from_request,
    )

    request = types.SimpleNamespace(
        state=types.SimpleNamespace(
            verified_identity=types.SimpleNamespace(email="unmapped@example.invalid")
        )
    )

    schema, reason = _resolve_schema_from_request(request)

    assert schema is None
    assert reason == UNRESOLVED_NO_MAPPING
