# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's portal welcome against real PostgreSQL.

Proves the platform store round-trips through
``platform.portal_welcome_messages`` and that it is keyed on the practice:
one practice's welcome is never read back for another, and resetting one
leaves the other alone. The unit suite (``tests/test_portal_welcome.py``)
covers the in-memory store and the route wiring.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from app.db import DEFAULT_PRACTICE_SCHEMA, PLATFORM_SCHEMA
from app.db.platform_models import PlatformBase
from app.portal.welcome import PortalWelcome
from app.portal.welcome_store import PlatformPortalWelcomeStore
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="module", autouse=True)
def _platform_tables() -> Iterator[None]:
    eng = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    with eng.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}"))
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DEFAULT_PRACTICE_SCHEMA}"))
        PlatformBase.metadata.create_all(conn)
    yield
    eng.dispose()


def _practice_id() -> str:
    return f"practice-welcome-{uuid.uuid4().hex[:8]}"


def test_a_practice_with_no_row_gets_none() -> None:
    assert PlatformPortalWelcomeStore().get(_practice_id()) is None


def test_save_round_trips_plain_text_and_overwrites() -> None:
    store = PlatformPortalWelcomeStore()
    practice = _practice_id()

    store.save(practice, PortalWelcome(heading="  First  ", body="One\n<b>x</b>\n"))
    assert store.get(practice) == PortalWelcome(heading="First", body="One\n<b>x</b>")

    store.save(practice, PortalWelcome(heading="Second", body="Two"))
    assert store.get(practice) == PortalWelcome(heading="Second", body="Two")


def test_one_practices_welcome_is_never_read_for_another() -> None:
    store = PlatformPortalWelcomeStore()
    ours, theirs = _practice_id(), _practice_id()

    store.save(ours, PortalWelcome(heading="Ours", body="Only ours."))
    assert store.get(theirs) is None

    store.save(theirs, PortalWelcome(heading="Theirs", body="Only theirs."))
    store.reset(ours)

    assert store.get(ours) is None
    assert store.get(theirs) == PortalWelcome(heading="Theirs", body="Only theirs.")
