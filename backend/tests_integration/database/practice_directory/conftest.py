# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice, calendar-key, and remembered-match fixtures."""

from __future__ import annotations

import base64
import os
import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

from .helpers import (
    _A,
    _B,
    _CALENDAR,
    _FAMILY,
    _Chart,
    _chart,
    _drop,
    _new_schema,
)
from .matching import (
    _repos,
    _series_key,
    _tenant_session,
)


@pytest.fixture(scope="module", autouse=True)
def _calendar_key() -> Iterator[None]:
    """The secret identifiers are digested under; the remembered answer needs it.

    One key for the module: ``remembered_as_theirs`` stores a digest once,
    and every test after it has to read that digest back under the same key.
    """
    from app.settings import get_settings

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
        get_settings.cache_clear()
        yield
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def practice(engine: Engine) -> Iterator[dict[str, str]]:
    """One practice, two clinicians. ``shared_*`` share a family email."""
    schema = _new_schema(engine, "directory")
    charts = {
        "schema": schema,
        "mine": _chart(engine, schema, _A, _Chart("Ada", "Lovelace")),
        "theirs": _chart(engine, schema, _B, _Chart("Grace", "Hopper")),
        "shared_mine": _chart(engine, schema, _A, _Chart("Ana", "Ruiz", _FAMILY)),
        "shared_theirs": _chart(engine, schema, _B, _Chart("Leo", "Ruiz", _FAMILY)),
        "deleted": _chart(engine, schema, _B, _Chart("Gone", "Away", deleted=True)),
        "pending": _chart(engine, schema, _B, _Chart("Not", "Yet", status="pending")),
    }
    yield charts
    _drop(engine, schema)


@pytest.fixture
def throwaway_role(engine: Engine) -> Iterator[str]:
    role = f"race_directory_{uuid.uuid4().hex[:8]}"
    yield role
    with engine.connect() as conn:
        conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        conn.commit()


@pytest.fixture(scope="module")
def remembered_as_theirs(engine: Engine, practice: dict[str, str]) -> None:
    """A answered the "rec-1" series as B's client: the strong evidence a block rests on."""
    from app.calendar_providers.source_identity import GOOGLE_CALENDAR_SOURCE
    from app.patients.identifiers import identifier_digest
    from app.repositories.patient_source_mapping import PatientSourceMapping

    conn, session = _tenant_session(engine, practice["schema"], _A)
    try:
        _, mappings = _repos(session)
        mappings.save(
            PatientSourceMapping(
                _CALENDAR,
                GOOGLE_CALENDAR_SOURCE,
                identifier_digest(_series_key()),
                practice["theirs"],
                _A,
            )
        )
        # The session joined the connection's transaction; the connection commits it.
        session.flush()
        conn.commit()
    finally:
        session.close()
        conn.close()
