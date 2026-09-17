# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform schema baseline — apply platform_template.sql

The origin of the platform schema. Applies
``backend/app/db/platform_template.sql``, which is a capture of the schema as it
actually stood when this chain was introduced, so a fresh install gets every
object the old two-builder arrangement produced between them.

**Why a captured file rather than ``op.create_table`` calls.** The platform
schema was built by ``PlatformBase.metadata.create_all``, with its evolution
scattered through the *tenant* chain. ``create_all`` emits tables, columns and
indexes and nothing else, so an authored baseline generated from the models
would have silently dropped, measured at the time of writing: 3 CHECK
constraints, the ``panel_applications`` row policy and the RLS switches behind
it, the pentest trigger and its function, and 2 partial indexes on
``practices`` that no model declares. The reverse diff was empty — ``create_all``
contributed nothing the migrations did not. A capture keeps all of it without
anyone having to notice each one. See PABLO-k7it, and
``backend/scripts/regen_platform_schema.py``.

**Existing databases are STAMPED at this revision, not run through it.** They
already have the schema. The DDL here is deliberately plain — no
``IF NOT EXISTS`` — because a guarded baseline would run green against a table
whose shape had already drifted, which is the failure this chain exists to end.
So the choice is explicit: fresh installs run it, existing installs stamp it,
and drift in the ones that stamp is repaired by later revisions rather than
hidden by a guard. ``app.db.platform_bootstrap`` decides which case it is.

Revision ID: a1b7c3d95e24
Revises:
Create Date: 2026-09-13
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# Alembic reads the module-level globals below by name via runtime
# introspection. ``__all__`` marks them as intentional exports so static
# analyzers (github-code-quality, vulture, etc.) don't flag them as
# "unused global variable" — every migration in the repo carries the
# same shape.
__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "a1b7c3d95e24"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``backend/alembic_platform/versions/x.py`` → ``backend/app/db/…``. The file
#: ships with the package the same way ``tenant_template.sql`` does, which
#: ``app.db.provisioning`` has read at runtime since the tenant template landed.
_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "app" / "db" / "platform_template.sql"

PLATFORM_SCHEMA = "platform"


def upgrade() -> None:
    """Build the platform schema from the captured template."""
    sql = _TEMPLATE_PATH.read_text()

    conn = op.get_bind()
    conn.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}")
    # Unqualified references inside function bodies, defaults and check
    # constraints resolve against this rather than against whatever schema the
    # pool's connection last touched.
    conn.exec_driver_sql(f"SET search_path = {PLATFORM_SCHEMA}, public")
    # pg_dump orders functions before the tables their bodies reference. Defer
    # body validation until execution, which is what pg_restore does.
    conn.exec_driver_sql("SET check_function_bodies = off")

    # Straight to a DBAPI cursor with NO parameter argument, which is the only
    # way to run this file.
    #
    # ``text()`` is out because SQLAlchemy would parse bind params and trip on
    # the dollar-quoted function bodies. But ``exec_driver_sql`` is out too, and
    # less obviously: it always hands the driver a parameter collection, which
    # makes psycopg2 treat ``%`` as a placeholder — and this schema contains one,
    # in the pentest CHECK that pg_dump renders as
    # ``schema_name ~~ like_escape('practice\\_%', '')``. The failure names
    # nothing useful (``TypeError: immutabledict is not a sequence``) and points
    # at SQLAlchemy internals rather than at a percent sign in a constraint.
    # psycopg2 only interpolates when parameters are passed, so passing none is
    # what keeps a literal ``%`` literal.
    #
    # ``app.db.provisioning._apply_tenant_template`` calls ``exec_driver_sql``
    # for the tenant template and is fine only because that file happens to
    # contain no ``%`` — the first tenant-side CHECK using LIKE would break
    # provisioning the same way.
    cursor = conn.connection.cursor()
    cursor.execute(sql)


def downgrade() -> None:
    """Refuse.

    Downgrading past the baseline means dropping the schema that holds every
    practice, user and identity in the deployment. There is no version of this
    migration where doing that as a side effect of an ``alembic downgrade`` is
    correct, so it is not offered — an operator who genuinely wants the schema
    gone can say so in SQL, where it reads as the decision it is.
    """
    msg = (
        "Refusing to downgrade past the platform baseline: it would drop the "
        "schema holding every practice, user and identity. Drop it explicitly "
        "in SQL if that is genuinely what you want."
    )
    raise RuntimeError(msg)
