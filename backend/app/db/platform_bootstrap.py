# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Deciding whether a database needs the platform baseline run, or just stamped.

The platform chain's baseline applies ``platform_template.sql`` with plain
``CREATE TABLE``. That is deliberate — a baseline written with
``IF NOT EXISTS`` would run green against a table whose shape had already
drifted, which is the failure the chain exists to end — but it means the baseline
must not be *run* against a database that already has the schema.

Almost every database does. The platform schema has existed since long before
the chain, built by ``PlatformBase.metadata.create_all``, so the ordinary case is
"the schema is here, the bookkeeping is not". That database is stamped at the
baseline and picks up later revisions normally. Only a genuinely empty database
runs the baseline for real.

**Why this is automatic rather than an operator step.** The alternative is a
manual stamp that every existing deployment needs exactly once, discovered by
hitting a failed migration — including for a self-hoster who has no idea the step
exists. ``bin/migrate.py`` already carries the same argument for the
single-practice migration: the migrate job runs before the rollout, with the
database in front of it and its output in the log, which is the right place for a
decision that has to be made once and made correctly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

from . import PLATFORM_SCHEMA

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

#: The platform chain's first revision. Must match the ``revision`` in
#: ``backend/alembic_platform/versions/a1b7c3d95e24_platform_schema_baseline.py``;
#: an integration test asserts the two agree, because a stamp pointing at a
#: revision the chain does not contain fails later and far from here, as
#: ``Can't locate revision identified by …`` on the next upgrade.
BASELINE_REVISION = "a1b7c3d95e24"

#: The chain's bookkeeping table, in the platform schema.
VERSION_TABLE = "alembic_version_platform"


def _platform_table_count(engine: Engine) -> int:
    """How many base tables the platform schema has, the version table aside."""
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables"
                    " WHERE table_schema = :schema AND table_type = 'BASE TABLE'"
                    " AND table_name <> :version_table"
                ),
                {"schema": PLATFORM_SCHEMA, "version_table": VERSION_TABLE},
            ).scalar_one()
        )


def _has_table(engine: Engine, table_name: str) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables"
                    " WHERE table_schema = :schema AND table_name = :table_name"
                ),
                {"schema": PLATFORM_SCHEMA, "table_name": table_name},
            ).scalar_one()
        )


#: Tables ``require_platform_schema`` insists on, rather than "at least one".
#:
#: A count is too weak a test, and weak in the specific way this whole change is
#: about. A database carrying two platform tables and not the rest passes "more
#: than zero" and then fails further along, on a foreign key into
#: ``platform.users`` or a seed INSERT into ``platform.icd10_codes`` — a check
#: that lets a half-built schema through has only moved the error, not caught it.
#:
#: These four are what the tenant chain and its seeding actually need: two for
#: the foreign keys every platform-referencing revision declares, two for the
#: diagnostic reference data env.py seeds.
_REQUIRED_TABLES = ("users", "practices", "icd10_codes", "diagnostic_definitions")


class PlatformSchemaMissingError(RuntimeError):
    """The platform schema is not there, and this code path does not build it."""


def bring_platform_to_head(engine: Engine, alembic_ini: str = "alembic.ini") -> None:
    """Create or migrate the platform schema, whichever this database needs.

    Called by ``bin/migrate.py`` before it runs the tenant chain. Everything else
    that needs both chains — the template regen, the Makefile, the integration
    tests — runs ``alembic -n platform upgrade head`` itself, in a subprocess,
    for the same reason this is not called from the tenant chain's ``env.py``:
    alembic's ``context`` and ``op`` are module-level proxies, so a nested
    ``command.upgrade`` tears down the outer environment's globals on exit and the
    run dies with ``KeyError: 'config'``.

    Not called at boot either. Schema changes belong to the migrate job, which
    runs before the rollout with its output in the log; boot checks and refuses.
    See :func:`require_platform_schema`.

    Once the chain is at head this also reconciles practice owners
    (:func:`app.db.practice_owner.reconcile_practice_owners`) — a one-off for
    rows registered before an owner was ever recorded, which is here for the
    same reason the rest of this module is.
    """
    # Imported here: alembic is a deploy-time dependency of this function, not of
    # the module, and ``app.db`` is imported by everything.
    from alembic import command
    from alembic.config import Config

    config = Config(alembic_ini, ini_section="platform")

    # The connection is handed to alembic rather than left to env.py, which
    # otherwise opens its own from ``settings.database_url``. That difference is
    # invisible until the two disagree, and then it is silent and wrong: called
    # against a scratch database, this migrated the shared one and reported
    # success, leaving the caller's database empty. Injecting the connection makes
    # the function operate on the engine its signature names.
    # Decided before the write transaction opens, on its own connection, so the
    # answer is about the database as it stands rather than as this call is
    # partway through changing it.
    stamp_first = needs_baseline_stamp(engine)

    with engine.begin() as connection:
        # The schema itself, because an injected connection makes env.py skip its
        # own bootstrap — it assumes a caller that supplies the connection has
        # prepared the database. Without this, alembic's first act is to create
        # its version table in a schema that does not exist.
        connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}")

        config.attributes["connection"] = connection

        if stamp_first:
            logger.info(
                "Platform schema exists with no chain bookkeeping — stamping the "
                "baseline (%s) rather than rebuilding it.",
                BASELINE_REVISION,
            )
            command.stamp(config, BASELINE_REVISION)

        command.upgrade(config, "head")

    # After the chain, and in its own transaction, because this is data rather
    # than schema: a failure here leaves the schema work committed and still
    # fails the job, which is the honest pair. Practices registered before the
    # sign-in path started recording owners learn theirs here, once, from the
    # email they were registered under.
    from .practice_owner import reconcile_practice_owners

    reconcile_practice_owners(engine)


def require_platform_schema(engine: Engine) -> None:
    """Fail loudly when the platform schema has not been built yet.

    Two callers, same question. Boot (``ensure_schemas``) used to BUILD it with
    ``create_all``, which is what let a platform table exist without the policy,
    trigger or constraint that was supposed to come with it. The tenant chain's
    ``env.py`` used to rely on that same call as the thing that satisfied its
    foreign keys into ``platform.users``.

    Neither builds it now, so both check. A missing schema is an operator error
    with one specific fix, and saying so beats the two ways it otherwise
    surfaces: a crashloop whose first symptom is ``relation "platform.users"
    does not exist`` inside a request, or a tenant migration failing on a
    foreign key to a table nobody created.
    """
    missing = [name for name in _REQUIRED_TABLES if not _has_table(engine, name)]
    if not missing:
        return

    present = _platform_table_count(engine)
    msg = (
        f"The {PLATFORM_SCHEMA!r} schema is missing {', '.join(missing)} "
        f"({present} platform table(s) present). It is built by the platform "
        "chain, which has to run before the tenant chain and is not run at "
        "boot:\n"
        "    python backend/bin/migrate.py        # does both, in order\n"
        "    alembic -n platform upgrade head     # the platform chain alone\n"
        "\n"
        "Nothing builds it from ORM metadata any more, deliberately: a schema "
        "built that way silently omits every policy, trigger, CHECK and grant "
        "that no SQLAlchemy model can express."
    )
    raise PlatformSchemaMissingError(msg)


def needs_baseline_stamp(engine: Engine) -> bool:
    """True when the platform schema exists but the chain has never been recorded.

    That is the pre-chain database: built by ``create_all``, evolved by raw SQL
    in the tenant chain, and carrying no platform bookkeeping of its own. It must
    be stamped rather than migrated from base, because the baseline would try to
    create tables it already has.

    False in the two other cases, which both want no stamp:

    * An empty database — nothing to preserve, so the baseline runs for real.
    * A database already carrying ``alembic_version_platform`` — the chain owns
      it, and alembic knows where it stands.
    """
    if _has_table(engine, VERSION_TABLE):
        return False
    return _platform_table_count(engine) > 0
