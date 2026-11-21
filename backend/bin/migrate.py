"""Cloud Run migrate-job entrypoint — chdir into backend/ then run alembic.

The production image WORKDIR is /app; alembic.ini and the migrations tree
live at /app/backend/. Alembic's `prepend_sys_path = .` resolves against
the working directory, so we chdir before delegating to the CLI.

Default args = `upgrade head`. Override by passing args to the Cloud Run
job (e.g. ``--args=backend/bin/migrate.py,downgrade,-1``).

The platform chain runs first and the tenant chain second, because tenant tables
carry foreign keys into ``platform.users`` and ``platform.practices`` and cannot
be built before the schema they reference. That ordering lives in the tenant
chain's own ``env.py``, which calls ``app.db.platform_bootstrap.bring_platform_to_head``
before migrating — so it holds for every caller (`make migrate`, the template
regen, the tests), not only for this entrypoint.

After a successful upgrade this also moves a pre-provisioning deployment onto
its own practice schema, if it is still on the template (see
``_run_single_practice_migration`` for why that belongs here rather than in the
boot path or in an operator's hands), and then provisions: the template, and on
an empty database the deployment's own practice (``app.db.provisioning.
ensure_schemas``), and then gives each mapped account its practice membership
(``_backfill_practice_members``). The app itself touches no database when it starts, so this
step has to run before it does — on a fresh install as on every upgrade.
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


def _provision_schemas() -> None:
    """Provision the template and, on an empty database, the deployment's practice.

    Runs after the practice migration, which has to move an old install off the
    template first: provisioning refuses a registry that still points there.
    Idempotent, so an existing deployment does nothing. Not guarded: a refusal
    here fails the migrate job, which is what keeps a revision that could not
    serve from rolling out.
    """
    from app.db import get_engine  # noqa: PLC0415
    from app.db.provisioning import ensure_schemas  # noqa: PLC0415

    ensure_schemas(get_engine())


def _remove_memberships_left_by_revokes() -> int:
    """Delete memberships whose grant was revoked before revoking removed them.

    A signed-in account resolves from its membership, so one left behind by an
    earlier revoke would route the removed account back into the practice it
    was removed from (``app.db.practice_members.remove_unmapped_claims``).
    Idempotent; a fresh install has nothing to remove.

    A process exit code. A failure fails the job, so that a revision which
    routes by membership does not roll out over rows that would re-admit
    removed accounts. Prints a count only, never an address or an id, for the
    reason ``_backfill_practice_members`` prints rather than logs.
    """
    from app.db import get_engine  # noqa: PLC0415
    from app.db.practice_members import remove_unmapped_claims  # noqa: PLC0415

    try:
        removed = remove_unmapped_claims(get_engine())
    except Exception as exc:
        # Type only: a database error message can carry row values.
        line = f"practice_members cleanup failed ({type(exc).__name__})"
        print(line, flush=True)
        logger.error("%s", line)
        return 1
    print(
        f"practice_members cleanup: {removed} memberships left by revoked grants removed",
        flush=True,
    )
    return 0


def _backfill_practice_members() -> None:
    """Give every account whose address maps to a practice a membership.

    ``platform.practice_members`` is recorded beside the email mapping, which
    still decides where an account signs in; a first sign-in writes an
    account's row, and accounts mapped before the table existed get theirs
    here (``app.db.practice_members.backfill``). Idempotent: an account with a
    membership is left alone. A fresh install has no accounts and does nothing.

    Prints how many accounts still have a mapping and no membership afterwards,
    which should be zero. Printed rather than logged at INFO, because alembic's
    logging config raises the root logger to WARNING for the rest of the job.
    Anything other than zero, or a failure, is also an ERROR to read and not a
    reason to stop the deploy: nothing routes by membership yet.
    """
    from app.db import get_engine  # noqa: PLC0415
    from app.db.practice_members import backfill  # noqa: PLC0415

    try:
        report = backfill(get_engine())
    except Exception as exc:
        # Type only: a database error message can carry row values.
        line = f"practice_members backfill failed ({type(exc).__name__})"
        print(line, flush=True)
        logger.error("%s", line)
        return
    line = (
        f"practice_members backfill: {report.inserted} inserted; "
        f"{report.unmembered} mapped accounts without a membership"
    )
    print(line, flush=True)
    if report.unmembered:
        logger.error("%s", line)


def _after_upgrade() -> int:
    """The practice migration, provisioning, the membership cleanup, then the backfill.

    A process exit code.
    """
    rc = _run_single_practice_migration()
    if rc != 0:
        return rc
    _provision_schemas()
    rc = _remove_memberships_left_by_revokes()
    if rc != 0:
        return rc
    _backfill_practice_members()
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
    # Not guarded by try/except: the tenant chain declares foreign keys into
    # ``platform.users``, so if the platform schema cannot be brought to head the
    # job should fail here with the traceback rather than further along, on a
    # constraint against a table nobody created.
    if _is_upgrade(args):
        # ``app`` is importable here only because of the line above this block.
        #
        # ``prepend_sys_path = .`` in alembic.ini is what normally puts backend/
        # on the path, and alembic applies it when IT loads the config — which
        # happens in ``main()``, below. ``_run_single_practice_migration`` imports
        # ``app.db`` after that call and so never noticed; this block runs before
        # it and died with ``ModuleNotFoundError: No module named 'app'``, which
        # would have taken the Cloud Run migrate job down on the next deploy.
        # Relying on another component's side effect for something this module
        # needs itself is the same mistake, one layer up, as the one this change
        # is unwinding.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        # Imported here rather than at module scope: ``app.db`` reads settings on
        # import, and the chdir above has to happen first.
        from app.db import get_engine
        from app.db.platform_bootstrap import bring_platform_to_head

        logger.info("Bringing the platform schema to head…")
        bring_platform_to_head(get_engine())

    rc = main(argv=args)
    # ``alembic.config.main`` returns None on success and raises or returns
    # non-zero otherwise; treat anything falsy as success so the practice
    # migration runs only after the schema is actually at head.
    if rc:
        sys.exit(rc)

    sys.exit(_after_upgrade() if _is_upgrade(args) else 0)
