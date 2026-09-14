"""Cloud Run migrate-job entrypoint — chdir into backend/ then run alembic.

The production image WORKDIR is /app; alembic.ini and the migrations tree
live at /app/backend/. Alembic's `prepend_sys_path = .` resolves against
the working directory, so we chdir before delegating to the CLI.

Default args = `upgrade head`. Override by passing args to the Cloud Run
job (e.g. ``--args=backend/bin/migrate.py,downgrade,-1``).

An upgrade runs the **platform** chain first and then the tenant chain, because
tenant tables carry foreign keys into ``platform.users`` and ``platform.practices``
and so cannot be built before the schema they reference. See
``_run_platform_chain``.

After a successful upgrade this also moves a pre-provisioning deployment onto
its own practice schema, if it is still on the template. See
``_run_single_practice_migration`` for why that belongs here rather than in the
boot path or in an operator's hands.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

#: Alembic's global options that consume the token after them. Knowing this set
#: is what lets the scan below find the subcommand: without it, the value of
#: ``-x`` or ``-n`` is just another non-flag token sitting in front of the
#: command. ``--opt=value`` needs no entry — the value travels inside the token.
_VALUE_OPTIONS = frozenset({"-c", "--config", "-n", "--name", "-x"})


def _is_upgrade(argv: list[str]) -> bool:
    """Whether this invocation is moving the schema forward.

    Two near-misses this deliberately handles, because both read as "the first
    thing that is not a flag" and neither is the command:

    * ``-x foo=bar upgrade head`` — ``foo=bar`` comes first.
    * ``-n some_name downgrade -1`` — ``some_name`` comes first, and if that
      name happened to be ``upgrade``, a scan looking for the *word* would run
      a schema rename on a downgrade.

    So option values are skipped by name rather than guessed at by shape.
    Anything other than ``upgrade`` leaves the practice migration alone: a
    rename is not something to do on the way back down, and an operator running
    ``history`` is not asking to migrate.
    """
    skip_next = False
    for arg in argv:
        if skip_next:
            skip_next = False
            continue
        if arg in _VALUE_OPTIONS:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        return arg == "upgrade"
    return False


def _run_platform_chain() -> None:
    """Bring the ``platform`` schema to head, stamping first if it predates the chain.

    Runs before the tenant chain: tenant tables reference ``platform.users`` and
    ``platform.practices``, so the schema holding them has to be there first.

    The stamp is the interesting half. The baseline applies a captured template
    with plain ``CREATE TABLE`` — no ``IF NOT EXISTS``, so that it cannot run
    green against a table that has already drifted — which means it must not be
    run against a database that already has the schema, and almost every database
    does. ``needs_baseline_stamp`` tells the two apart, and the stamp records
    "you already have this" without touching a table.

    Raises on failure rather than returning a code, so a platform chain that
    cannot be applied stops the job before the tenant chain runs against a schema
    it depends on.
    """
    # Imported here, not at module scope: ``app.db`` reads settings on import,
    # and this module has to ``chdir`` into backend/ first so alembic's
    # ``prepend_sys_path`` resolves.
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415
    from app.db import get_engine  # noqa: PLC0415
    from app.db.platform_bootstrap import (  # noqa: PLC0415
        BASELINE_REVISION,
        needs_baseline_stamp,
    )

    config = Config("alembic.ini", ini_section="platform")

    engine = get_engine()
    if needs_baseline_stamp(engine):
        logger.info(
            "Platform schema exists with no chain bookkeeping — stamping the "
            "baseline (%s) rather than rebuilding it.",
            BASELINE_REVISION,
        )
        command.stamp(config, BASELINE_REVISION)

    logger.info("Running platform alembic upgrade head…")
    command.upgrade(config, "head")


def _run_single_practice_migration() -> int:
    """Move this deployment onto its own practice schema, if it is not already.

    **Why this runs automatically, and here.** A deployment created before the
    template and the live practice were separated keeps its charts in
    ``practice`` — the schema that is also the provisioning template, and the one
    ``enable_rls_on_schema`` skips. It therefore runs with no row policies at
    all, and boot refuses to serve it.

    The obvious alternatives are both worse. Doing it at BOOT runs an
    irreversible rename where nobody is watching, and a refusal there reads as a
    crashloop. Leaving it to an operator means every upgrade path has a manual
    step in the middle that is only discovered by hitting the boot refusal —
    including for a self-hoster who has no idea the step exists.

    The migrate job is the right place precisely because it is neither: it runs
    before the rollout, with the database in front of it and its output in the
    log, and it is where every other schema change already happens. A deployment
    that would lose a chart fails HERE, with nothing deployed and the database
    untouched, rather than after a new revision is live.

    Idempotent by the same check boot uses, so an already-migrated deployment
    (and every fresh install, which boots straight onto its own practice) does
    nothing and says nothing.

    Returns a process exit code: 0 for done-or-nothing-to-do, 1 for a pre-flight
    that refuses. Refusing fails the migrate job, which stops the deploy — the
    intended outcome, because the alternative is rolling out a revision that
    cannot serve.
    """
    # Imported here, not at module scope: ``app.db`` reads settings on import,
    # and this module has to ``chdir`` into backend/ first so alembic's
    # ``prepend_sys_path`` resolves.
    from app.db import get_engine  # noqa: PLC0415
    from app.db.single_practice_migration import (  # noqa: PLC0415
        PreflightError,
        format_report,
        is_migrated,
        migrate,
        preflight,
    )

    engine = get_engine()
    if is_migrated(engine):
        return 0

    logger.info(
        "This deployment is still registered against the provisioning template; "
        "moving it onto its own practice schema."
    )
    logger.info("Pre-flight:\n%s", format_report(preflight(engine)))

    try:
        migrate(engine)
    except PreflightError as exc:
        # Deliberately not --force. The migrate job failing is what stops the
        # rollout, and a deployment whose charts would go invisible should not
        # be deployed by a job that decided for itself that was acceptable.
        logger.error("%s", exc)
        return 1

    logger.info("Migrated onto the deployment's own practice schema.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    os.chdir(Path(__file__).resolve().parent.parent)
    from alembic.config import main

    args = sys.argv[1:] or ["upgrade", "head"]

    # Platform chain first, and only when moving forward. A ``downgrade`` is not
    # an instruction to upgrade a different schema, and an operator running
    # ``history`` is not asking to migrate anything — the same reasoning
    # ``_is_upgrade`` already encodes for the practice migration below.
    #
    # Not guarded by try/except: if the platform schema cannot be brought to
    # head, the tenant chain must not run against it, and the job should fail
    # here with the traceback rather than further along with a missing table.
    if _is_upgrade(args):
        _run_platform_chain()

    rc = main(argv=args)
    # ``alembic.config.main`` returns None on success and raises or returns
    # non-zero otherwise; treat anything falsy as success so the practice
    # migration runs only after the schema is actually at head.
    if rc:
        sys.exit(rc)

    sys.exit(_run_single_practice_migration() if _is_upgrade(args) else 0)
