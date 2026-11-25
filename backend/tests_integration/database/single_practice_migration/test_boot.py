# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Test boot for single-practice conversion."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from app.db import (
    DEFAULT_PRACTICE_OWN_SCHEMA,
    DEFAULT_PRACTICE_SCHEMA,
)
from app.db.provisioning import ensure_schemas
from app.db.single_practice_migration import (
    is_migrated,
    migrate,
)

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
    from sqlalchemy.engine import Engine


def test_boot_refuses_against_an_unmigrated_deployment(old_shape: Engine) -> None:
    """Criterion 5: boot fails with an actionable message, not a half-state.

    This used to warn and carry on, which meant serving a chart schema with no
    row policies while the deployment looked healthy. A warning in a startup log
    is not a control — nobody is reading it.
    """

    engine = old_shape

    with pytest.raises(RuntimeError) as exc:
        ensure_schemas(engine)

    message = str(exc.value)
    assert "migrate_default_practice" in message, message
    assert DEFAULT_PRACTICE_SCHEMA in message
    assert DEFAULT_PRACTICE_OWN_SCHEMA in message


def test_boot_is_happy_once_migrated(old_shape: Engine) -> None:
    """And the refusal lifts by doing the thing it asked for — otherwise it is
    just a deployment that can never start."""

    engine = old_shape
    with engine.begin() as conn:
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, str(uuid.uuid4()))

    migrate(engine)
    ensure_schemas(engine)  # must not raise

    assert is_migrated(engine)
