# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Test identities for single-practice conversion."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from app.db import (
    DEFAULT_PRACTICE_ID,
    DEFAULT_PRACTICE_SCHEMA,
    PLATFORM_SCHEMA,
)
from app.db.single_practice_migration import (
    backfill_identity_mappings,
    migrate,
    unresolvable_identities,
)
from sqlalchemy import text

from .helpers import _seed_patient

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)


if TYPE_CHECKING:
    from .types import IdentityFixture


def test_an_unmapped_identity_is_reported_as_unresolvable(identities: IdentityFixture) -> None:
    """Before ``AllowlistRepository.add`` wrote the mapping alongside the grant,
    an operator added an email and that was the whole story. Those rows resolve
    to nothing — survivable while resolution was skippable, a locked-out user
    once it is not."""
    engine, seed = identities
    stranded = seed("legacy-user@example.com", as_user=True, with_mapping=False)
    seed("already-mapped@example.com", as_user=True, with_mapping=True)

    unresolvable = unresolvable_identities(engine)

    assert stranded in unresolvable
    assert "already-mapped@example.com" not in unresolvable


def test_a_granted_but_unregistered_email_also_counts(identities: IdentityFixture) -> None:
    """Somebody granted access who has not signed up yet still has to be able to
    when they do — ``allowed_emails`` is half the population, not a footnote."""
    engine, seed = identities
    invited = seed("invited@example.com", as_user=False, with_mapping=False)

    assert invited in unresolvable_identities(engine)


def test_the_backfill_maps_everyone_and_is_idempotent(identities: IdentityFixture) -> None:
    engine, seed = identities
    seed("a@example.com", as_user=True, with_mapping=False)
    seed("b@example.com", as_user=False, with_mapping=False)
    seed("c@example.com", as_user=True, with_mapping=True)

    first = backfill_identity_mappings(engine)
    assert first >= 2  # a and b; the fixture database may hold others

    assert unresolvable_identities(engine) == []

    # Second pass finds nothing — the INSERT selects only unmapped rows.
    assert backfill_identity_mappings(engine) == 0


def test_the_backfill_writes_the_same_shape_a_grant_would(identities: IdentityFixture) -> None:
    """A backfilled row must be indistinguishable from one ``add`` wrote, or the
    login path will treat two populations differently."""
    engine, seed = identities
    email = seed("shape@example.com", as_user=True, with_mapping=False)

    backfill_identity_mappings(engine)

    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT tenant_id, practice_id FROM {PLATFORM_SCHEMA}.email_tenant_mappings "  # noqa: S608 - PLATFORM_SCHEMA is a module constant, not input
                f"WHERE email = :e"
            ),
            {"e": email},
        ).one()
    assert row == (DEFAULT_PRACTICE_ID, DEFAULT_PRACTICE_ID)


def test_migrate_backfills_so_nobody_is_left_unresolvable(identities: IdentityFixture) -> None:
    """The end-to-end promise of the decision note: after migrating, every
    identity the platform knows about resolves to a practice."""
    engine, seed = identities
    seed("pre-existing@example.com", as_user=True, with_mapping=False)
    with engine.begin() as conn:
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, str(uuid.uuid4()))

    migrate(engine)

    assert unresolvable_identities(engine) == []
