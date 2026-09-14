# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The platform chain builds the platform schema, and builds it faithfully.

Three things are checked, and they are not the same thing:

1. ``alembic -n platform upgrade head`` succeeds against an empty database. The
   baseline applies ``platform_template.sql``, which is a real pg_dump — if that
   file does not execute, nothing else here matters.

2. **No model drift.** ``alembic -n platform check`` finds no difference between
   the schema the chain built and ``PlatformBase.metadata``. This is the gate
   that replaces what ``create_all`` was silently providing: as long as
   ``create_all`` ran at boot, a model column with no migration behind it simply
   appeared, and nothing ever reported the chain as incomplete. Now it fails
   here instead.

3. **The chain matches what the old two-builder arrangement produced**, object
   for object — every table, column, index, constraint, policy, trigger,
   function and RLS switch. This is the evidence that introducing the chain
   changed no schema, and it is deliberately a comparison against
   ``create_all`` + the tenant chain rather than against the models, because the
   models are the thing that was missing objects: 3 CHECK constraints, a row
   policy and its RLS switches, a trigger and its function, and 2 partial
   indexes on ``practices``.

   This test is TRANSITIONAL. It is the proof for the cutover, and it should be
   deleted in the change that removes ``create_all`` from the boot path — at
   that point there is no "legacy shape" left to compare against, and (2) is the
   gate that carries forward. See PABLO-k7it.

Requires ``DATABASE_URL`` + ``DATABASE_BACKEND=postgres`` and a role with
``CREATEDB``. Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.db.platform_bootstrap import BASELINE_REVISION, needs_baseline_stamp
from sqlalchemy import create_engine, text

from . import scratch_db

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_BACKEND_DIR = Path(__file__).resolve().parents[2]

#: The chain's bookkeeping table, which the legacy shape has no equivalent of.
#: Excluded from the comparison rather than special-cased inside each query.
_VERSION_TABLE = "alembic_version_platform"


def _alembic(database_url: str, *args: str) -> None:
    """Run alembic in a subprocess against ``database_url``.

    Subprocess because ``env.py`` reads ``settings.database_url`` at import, so
    an in-process call would reuse the first URL it ever saw.

    ``sys.executable -m alembic`` rather than ``poetry run alembic``: in a git
    worktree poetry resolves no environment and falls through to whatever
    ``alembic`` is first on PATH, which on a developer machine with anaconda
    installed is a Python 3.10 that dies importing ``app.db`` with
    ``TypeError: 'type' object is not subscriptable``.
    """
    env = {**os.environ, "DATABASE_URL": database_url, "DATABASE_BACKEND": "postgres"}
    result = subprocess.run(  # noqa: S603 (trusted: this interpreter, test-controlled args)
        [sys.executable, "-m", "alembic", *args],
        cwd=_BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"alembic {' '.join(args)} failed.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def empty_db() -> Iterator[str]:
    """A throwaway database with nothing in it."""
    db = scratch_db.scratch_name("pablo_platform_chain")
    admin = create_engine(_db_url, isolation_level="AUTOCOMMIT")
    try:
        scratch_db.create(admin, db)
        yield scratch_db.swap_database(_db_url, db)
    finally:
        scratch_db.drop(admin, db)
        admin.dispose()


@pytest.fixture
def legacy_db() -> Iterator[str]:
    """A throwaway database built the way a deploy built it before this chain.

    The tenant chain's ``env.py`` runs ``PlatformBase.metadata.create_all`` and
    then every tenant-chain migration on top, so ``alembic upgrade head`` on the
    default section reproduces the whole of it.
    """
    db = scratch_db.scratch_name("pablo_platform_legacy")
    admin = create_engine(_db_url, isolation_level="AUTOCOMMIT")
    try:
        scratch_db.create(admin, db)
        url = scratch_db.swap_database(_db_url, db)
        _alembic(url, "upgrade", "head")
        yield url
    finally:
        scratch_db.drop(admin, db)
        admin.dispose()


# --- reading a schema back, by object ---------------------------------------


def _columns(engine: Engine) -> set[tuple]:
    sql = text(
        """
        SELECT table_name, column_name, data_type, is_nullable,
               coalesce(character_maximum_length, -1),
               coalesce(column_default, '')
        FROM information_schema.columns
        WHERE table_schema = 'platform' AND table_name <> :version_table
        """
    )
    with engine.connect() as conn:
        return {tuple(row) for row in conn.execute(sql, {"version_table": _VERSION_TABLE})}


def _indexes(engine: Engine) -> set[tuple[str, str]]:
    sql = text(
        """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'platform' AND tablename <> :version_table
        """
    )
    with engine.connect() as conn:
        return {(row[0], row[1]) for row in conn.execute(sql, {"version_table": _VERSION_TABLE})}


def _normalize_constraint_def(definition: str) -> str:
    """Fold the two renderings Postgres gives the same CHECK.

    A CHECK that SQLAlchemy emitted and the identical CHECK read back out of a
    pg_dump and re-parsed are stored as different text::

        = ANY ((ARRAY['therapist'::character varying, ...])::text[])   -- from DDL
        = ANY (ARRAY[('therapist'::character varying)::text, ...])     -- round-tripped

    Postgres canonicalizes the cast differently depending on which form it
    parsed, so comparing ``pg_get_constraintdef`` verbatim reports a difference
    where there is no difference in meaning. Dropping the casts and the
    punctuation leaves the part that would actually differ if the predicate
    changed — the columns, the operators and the literals.

    This is why the comparison is worth normalizing rather than loosening to
    match on name alone: a CHECK that genuinely changed shape still fails.
    """
    for cast in ("::text[]", "::text", "::character varying", "::bpchar"):
        definition = definition.replace(cast, "")
    return "".join(definition.split()).replace("(", "").replace(")", "")


def _constraints(engine: Engine) -> set[tuple[str, str, str]]:
    """Constraints by name, kind and normalized definition."""
    sql = text(
        """
        SELECT c.conname, c.contype, pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'platform' AND t.relname <> :version_table
        """
    )
    with engine.connect() as conn:
        return {
            (row[0], row[1], _normalize_constraint_def(row[2]))
            for row in conn.execute(sql, {"version_table": _VERSION_TABLE})
        }


def _policies(engine: Engine) -> set[tuple]:
    sql = text(
        """
        SELECT tablename, policyname, permissive, roles::text, cmd,
               coalesce(qual, ''), coalesce(with_check, '')
        FROM pg_policies WHERE schemaname = 'platform'
        """
    )
    with engine.connect() as conn:
        return {tuple(row) for row in conn.execute(sql)}


def _rls_flags(engine: Engine) -> set[tuple[str, bool, bool]]:
    sql = text(
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'platform' AND c.relkind = 'r'
          AND (c.relrowsecurity OR c.relforcerowsecurity)
        """
    )
    with engine.connect() as conn:
        return {(row[0], row[1], row[2]) for row in conn.execute(sql)}


def _triggers(engine: Engine) -> set[tuple[str, str, str]]:
    sql = text(
        """
        SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid)
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'platform' AND NOT t.tgisinternal
        """
    )
    with engine.connect() as conn:
        return {(row[0], row[1], row[2]) for row in conn.execute(sql)}


def _functions(engine: Engine) -> set[tuple[str, str]]:
    sql = text(
        """
        SELECT p.proname, pg_get_functiondef(p.oid)
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'platform'
        """
    )
    with engine.connect() as conn:
        return {(row[0], row[1]) for row in conn.execute(sql)}


_READERS = {
    "columns": _columns,
    "indexes": _indexes,
    "constraints": _constraints,
    "policies": _policies,
    "rls flags": _rls_flags,
    "triggers": _triggers,
    "functions": _functions,
}


# --- the tests ---------------------------------------------------------------


def test_upgrade_head_builds_the_schema_from_empty(empty_db: str) -> None:
    """The baseline applies the template, and the template executes.

    Worth its own test because the template is a pg_dump rather than
    ``op.create_table`` calls: it can fail for reasons a hand-written migration
    cannot, such as a function body referring to a table that the dump orders
    after it, or a literal ``%`` in a CHECK constraint being taken for a
    parameter placeholder.
    """
    _alembic(empty_db, "-n", "platform", "upgrade", "head")

    engine = create_engine(empty_db)
    try:
        with engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables"
                    " WHERE table_schema = 'platform' AND table_type = 'BASE TABLE'"
                )
            ).scalar_one()
            stamped = conn.execute(
                text(f"SELECT count(*) FROM platform.{_VERSION_TABLE}")  # noqa: S608
            ).scalar_one()
    finally:
        engine.dispose()

    # The count is deliberately a floor rather than an exact number: a test that
    # has to be edited every time a table is added stops being read and starts
    # being updated.
    assert tables > 15, f"platform schema has only {tables} tables"
    assert stamped == 1, "chain did not stamp exactly one revision"


def test_no_drift_between_the_chain_and_the_models(empty_db: str) -> None:
    """``alembic check`` finds nothing to generate.

    This is the check that ``create_all`` used to absorb. While it ran at boot,
    a model column with no migration behind it just appeared in the database and
    no test could tell; the chain would quietly be incomplete and the schema
    would still be right. Now the incompleteness is what fails.

    When this fails, the fix is a revision — ``alembic -n platform revision
    --autogenerate -m "..."`` — plus ``regen_platform_schema.py``. Not a change
    to this test.
    """
    _alembic(empty_db, "-n", "platform", "upgrade", "head")
    _alembic(empty_db, "-n", "platform", "check")


#: The only objects the chain is allowed to be missing relative to the legacy
#: shape: the 15 duplicate indexes that ``b2c8d4e06f31`` drops on purpose. Each
#: is covered by a surviving twin on the same table and columns — the list of
#: pairs is in that revision. Anything else missing is a fault in the baseline.
_INTENTIONALLY_DROPPED_INDEXES = frozenset(
    {
        "ix_booking_links_user_id",
        "ix_claim_routes_practice_id",
        "ix_companion_devices_jkt",
        "ix_companion_devices_user_id",
        "ix_launch_intents_expires_at",
        "ix_launch_intents_user_id",
        "ix_passkey_backup_codes_user_id",
        "ix_passkey_challenges_expires_at",
        "ix_passkey_challenges_user_id",
        "ix_passkey_credentials_user_id",
        "ix_platform_audit_logs_action",
        "ix_platform_audit_logs_actor",
        "ix_platform_audit_logs_tenant_schema",
        "ix_platform_audit_logs_timestamp",
        "ix_user_identities_user_id",
    }
)


def test_chain_matches_the_legacy_bootstrap(empty_db: str, legacy_db: str) -> None:
    """The chain reproduces the pre-chain schema, minus what it drops on purpose.

    Asserting "the difference is exactly the intended difference" rather than
    "there is no difference": the chain deliberately stops creating 15 duplicate
    indexes, and a test that merely allowed any divergence would also pass if the
    baseline had quietly lost the pentest trigger.

    TRANSITIONAL — see the module docstring.
    """
    _alembic(empty_db, "-n", "platform", "upgrade", "head")

    chain_engine = create_engine(empty_db)
    legacy_engine = create_engine(legacy_db)
    try:
        unexpected: list[str] = []
        accounted_for: set[str] = set()
        for label, read in _READERS.items():
            chain = read(chain_engine)
            legacy = read(legacy_engine)

            for item in sorted(legacy - chain, key=str):
                if label == "indexes" and item[0] in _INTENTIONALLY_DROPPED_INDEXES:
                    accounted_for.add(item[0])
                    continue
                unexpected.append(f"{label}: in legacy, MISSING from chain: {item}")

            for item in sorted(chain - legacy, key=str):
                unexpected.append(f"{label}: in chain, absent from legacy: {item}")
    finally:
        chain_engine.dispose()
        legacy_engine.dispose()

    assert not unexpected, (
        "chain-built platform schema differs from the legacy shape:\n" + "\n".join(unexpected)
    )

    # And the drops must actually have happened. Without this, the same test
    # passes if the duplicates were never created in the legacy shape either —
    # which would mean the thing being cleaned up had stopped existing and this
    # revision, and the list above, were both dead weight.
    assert accounted_for == set(_INTENTIONALLY_DROPPED_INDEXES), (
        "the legacy shape no longer carries every index b2c8d4e06f31 drops; "
        f"unaccounted for: {sorted(set(_INTENTIONALLY_DROPPED_INDEXES) - accounted_for)}"
    )


# --- the stamp-or-run decision -----------------------------------------------


def test_baseline_revision_constant_matches_the_chain() -> None:
    """``BASELINE_REVISION`` names the revision the chain actually starts at.

    Stamping a revision the chain does not contain fails later and somewhere
    else — as ``Can't locate revision identified by …`` on the next upgrade,
    against a database that by then has been recorded as migrated.
    """
    script = ScriptDirectory.from_config(
        Config(str(_BACKEND_DIR / "alembic.ini"), ini_section="platform")
    )
    bases = list(script.get_bases())

    assert bases == [BASELINE_REVISION], (
        f"platform chain bases are {bases}, but platform_bootstrap names {BASELINE_REVISION!r}"
    )


def test_empty_database_is_not_stamped_but_built(empty_db: str) -> None:
    """An empty database runs the baseline rather than being told it already has it."""
    engine = create_engine(empty_db)
    try:
        assert needs_baseline_stamp(engine) is False
    finally:
        engine.dispose()


def test_pre_chain_database_needs_a_stamp(legacy_db: str) -> None:
    """A database built the old way is recognised as needing the stamp.

    This is the case nearly every real deployment is in: the platform schema has
    been there for months, and nothing has ever recorded a platform revision
    against it.
    """
    engine = create_engine(legacy_db)
    try:
        assert needs_baseline_stamp(engine) is True
    finally:
        engine.dispose()


def test_a_stamped_database_does_not_ask_to_be_stamped_again(legacy_db: str) -> None:
    """Once stamped, the decision flips — so a second migrate job is a no-op.

    The stamp is the one irreversible-looking step in this change, and an
    idempotency bug here would mean every deploy re-stamped a database that had
    since moved forward, walking it backwards.
    """
    _alembic(legacy_db, "-n", "platform", "stamp", "a1b7c3d95e24")

    engine = create_engine(legacy_db)
    try:
        assert needs_baseline_stamp(engine) is False
    finally:
        engine.dispose()


def test_stamped_pre_chain_database_upgrades_to_head(legacy_db: str) -> None:
    """The whole path a real deployment takes: stamp, then upgrade.

    The payoff is the duplicate-index repair actually running — the baseline is
    skipped as already-present, and the revision after it does the work.
    """
    _alembic(legacy_db, "-n", "platform", "stamp", "a1b7c3d95e24")
    _alembic(legacy_db, "-n", "platform", "upgrade", "head")

    engine = create_engine(legacy_db)
    try:
        with engine.connect() as conn:
            surviving = {
                row[0]
                for row in conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE schemaname = 'platform'")
                )
            }
    finally:
        engine.dispose()

    still_there = sorted(_INTENTIONALLY_DROPPED_INDEXES & surviving)
    assert not still_there, f"duplicate indexes survived the upgrade: {still_there}"

    # The twins have to still be there, or this "repair" removed the only index
    # on those columns.
    for twin in (
        "ix_platform_booking_links_user_id",
        "ix_platform_platform_audit_logs_timestamp",
        "ix_platform_user_identities_user_id",
    ):
        assert twin in surviving, f"{twin} was dropped along with its duplicate"
