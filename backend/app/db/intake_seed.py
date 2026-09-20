# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The intake form a practice starts with.

Every practice gets one published form called "Intake" the moment its schema
exists: who you are, what brings you in, and the two screeners. That is the
same set of questions the fixed intake form has always asked, which is the
point — the form that shipped before packets existed becomes version 1 of
the default template rather than a second system sitting beside it.

Seeded here rather than in a migration, for the reason the tenant chain is
not what builds a fresh schema: provisioning applies the captured template
and stamps it at head, so a migration that inserted rows would run for
existing practices and never for new ones. This runs on exactly the path
that creates a schema.

``created_by`` and ``published_by`` are NULL. Nobody wrote this form or
pressed publish on it; it came with Pablo, and a NULL says so more honestly
than borrowing the id of whoever happened to sign up.

A practice edits it like any other form — rename it, add questions, publish
a version 2. Nothing re-seeds afterwards: the guard is "this schema has no
templates at all", so a practice that deletes every question still keeps its
own form rather than finding ours back.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import text

from ..utcnow import utc_now

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

#: The default form, in the order the patient answers it. Each entry is
#: ``(key, item_type, config)``. Deliberately the same four the fixed form
#: asked, so an existing practice's intake does not change shape under it.
DEFAULT_PACKET_NAME = "Intake"
DEFAULT_PACKET_ITEMS: tuple[tuple[str, str, dict[str, object]], ...] = (
    ("demographics", "demographics", {}),
    ("reason", "reason", {}),
    ("phq9", "instrument", {"code": "phq9"}),
    ("gad7", "instrument", {"code": "gad7"}),
)

_TEMPLATE_INSERT = text(
    "INSERT INTO intake_packet_templates (id, name, created_by, created_at, archived_at) "
    "VALUES (:id, :name, NULL, :now, NULL)"
)

_VERSION_INSERT = text(
    "INSERT INTO intake_packet_versions "
    "(id, template_id, version, published_at, published_by, created_at) "
    "VALUES (:id, :template_id, 1, :now, NULL, :now)"
)

_ITEM_INSERT = text(
    "INSERT INTO intake_item_definitions "
    "(id, version_id, key, position, item_type, required, config, resign_on_new_version) "
    "VALUES (:id, :version_id, :key, :position, :item_type, TRUE, "
    "CAST(:config AS jsonb), FALSE)"
)


def seed_default_intake_packet(engine: Engine, schema_name: str) -> None:
    """Lay down the default form in a freshly-provisioned schema.

    Idempotent: a schema that already has any template is left alone, so a
    retried provision cannot end up with two copies.
    """
    now = utc_now()
    template_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema_name}"))
        existing = conn.execute(text("SELECT count(*) FROM intake_packet_templates")).scalar()
        if existing:
            return

        conn.execute(_TEMPLATE_INSERT, {"id": template_id, "name": DEFAULT_PACKET_NAME, "now": now})
        conn.execute(_VERSION_INSERT, {"id": version_id, "template_id": template_id, "now": now})
        for position, (key, item_type, config) in enumerate(DEFAULT_PACKET_ITEMS):
            conn.execute(
                _ITEM_INSERT,
                {
                    "id": str(uuid.uuid4()),
                    "version_id": version_id,
                    "key": key,
                    "position": position,
                    "item_type": item_type,
                    "config": json.dumps(config),
                },
            )


__all__ = ["DEFAULT_PACKET_ITEMS", "DEFAULT_PACKET_NAME", "seed_default_intake_packet"]
