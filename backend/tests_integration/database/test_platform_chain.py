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

   Nothing in production builds a schema that way any more — the fixture is the
   only thing left that calls ``create_all`` on ``PlatformBase``, and it does so
   deliberately, to pin the captured template against the shape it was taken
   from. Keep it: without it, a later edit could quietly drop an object from the
   template and only (2) would notice, and (2) cannot see a policy or a trigger.

4. **Boot and the tenant chain both refuse** when the platform schema has not
   been built. Neither builds it now, and a clear refusal is the difference
   between "run the migrate job" and a crashloop whose first symptom is
   ``relation "platform.users" does not exist`` inside a request.

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
from app.db.platform_bootstrap import (
    BASELINE_REVISION,
    PlatformSchemaMissingError,
    bring_platform_to_head,
    needs_baseline_stamp,
    require_platform_schema,
)
from app.db.platform_models import PlatformBase
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

#: The tenant-chain revision immediately before ``d8f3b6c04e17``, the
#: duplicate-index cleanup. The legacy fixture stops here, because that is where
#: every real database currently stands.
_LAST_REVISION_BEFORE_THE_CLEANUP = "c4d81e6a2f09"


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

    ``create_all`` from the models, then every tenant-chain migration on top,
    which is exactly what the tenant chain's ``env.py`` used to do — the
    ``create_all`` call was in its bootstrap block, and is the reason the tenant
    chain could satisfy its own foreign keys into ``platform.users``.

    Reconstructed here rather than invoked: production no longer builds a schema
    this way, and this fixture is the only thing left that does. That is the
    point of it — it pins the captured template against the shape it was taken
    from, so the baseline cannot quietly lose an object later.
    """
    db = scratch_db.scratch_name("pablo_platform_legacy")
    admin = create_engine(_db_url, isolation_level="AUTOCOMMIT")
    try:
        scratch_db.create(admin, db)
        url = scratch_db.swap_database(_db_url, db)

        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE SCHEMA IF NOT EXISTS platform"))
            PlatformBase.metadata.create_all(engine)
        finally:
            engine.dispose()

        # The tenant chain on top, stopping one revision short of head — at
        # ``c4d81e6a2f09``, the last revision before the duplicate-index cleanup.
        # Its platform-schema statements are all ``IF NOT EXISTS``, so they layer
        # onto what create_all built, which is how the two builders came to
        # disagree in the first place.
        #
        # Stopping there is what makes this a reconstruction of a REAL database
        # rather than an approximation of one: every deployment has run these
        # revisions and none has yet run the cleanup, so this is the shape dev and
        # prod are in today. Running to head would drop the duplicates and leave
        # the comparison below proving strictly less.
        _alembic(url, "upgrade", _LAST_REVISION_BEFORE_THE_CLEANUP)

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


#: The clinician's credential record, which ``b6e2f8a41c37`` creates here and
#: the legacy shape never had — it lived in each practice schema until then, so
#: a bootstrap that reconstructs the pre-chain platform schema cannot contain
#: it. This is the mirror of the dropped-index allowance below: the chain is
#: permitted to have gained exactly these, and nothing else.
#:
#: Matched by name prefix rather than listed table by table, because every
#: object each one brings — its indexes, CHECK constraints, row policies and RLS
#: switches — is equally absent from legacy, and enumerating them would be a
#: second copy of the migration that goes stale the first time a column moves.
#: Tables the chain creates in ``platform`` that the legacy bootstrap — a
#: snapshot taken before either move — could not know about. Listed rather than
#: matched by prefix: ``credential_`` used to cover all of them, and would have
#: gone on silently absorbing any future table named that way, while these four
#: share no prefix at all. Editing this set is how the move gets acknowledged.
_MOVED_TO_PLATFORM = frozenset(
    {
        # b6e2f8a41c37 — the clinician's credential record.
        "credential_government_ids",
        "credential_licenses",
        "credential_liability_policies",
        "credential_education",
        "credential_training",
        "credential_employment",
        "credential_references",
        "credential_disclosures",
        "credential_confirmations",
        "credential_service_locations",
        "credential_bank_accounts",
        # a7c4e9b21f58 — her payer relationships.
        "payer_authorizations",
        "payer_participations",
        "payer_participation_events",
        "contracted_rates",
    }
)


def _is_credential_record(item: object) -> bool:
    """Does this schema object belong to a table that moved to platform?

    ``item`` is whatever a reader yields — a bare name, or a tuple whose first
    element is the table. Both shapes appear in ``_READERS``.
    """
    first = item[0] if isinstance(item, tuple) else item
    return isinstance(first, str) and first in _MOVED_TO_PLATFORM


#: The 15 duplicate indexes ``d8f3b6c04e17`` drops.
#:
#: Each is covered by a surviving twin on the same table and columns — the list of
#: pairs is in that revision. Verified against pablohealth-dev on 2026-09-14: all
#: 15 present, each definition identical to its twin's, every twin also present,
#: and no other duplicate groups anywhere in the platform schema.
#:
#: They are the only objects the chain-built schema is allowed to be missing
#: relative to the legacy one. Anything else missing is a fault in the baseline.
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

    See the module docstring for why the legacy shape is reconstructed rather
    than invoked.
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
                if _is_credential_record(item):
                    continue
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
    """The whole path a real deployment takes: stamp, then both chains in order.

    Both chains, because the duplicate-index repair is the payoff and it lives at
    the end of the TENANT chain — it has to, since eight revisions in that chain
    create the indexes and that chain runs second. A drop on the platform side is
    undone a moment later on every fresh install.
    """
    _alembic(legacy_db, "-n", "platform", "stamp", "a1b7c3d95e24")
    _alembic(legacy_db, "-n", "platform", "upgrade", "head")
    _alembic(legacy_db, "upgrade", "head")

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


def test_the_tenant_chain_does_not_put_the_duplicates_back(empty_db: str) -> None:
    """Running both chains in order leaves the duplicates dropped.

    The regression this exists for: eight tenant-chain revisions create these
    indexes with ``CREATE INDEX IF NOT EXISTS``, and the tenant chain runs second.
    With the cleanup on the platform side, a fresh install dropped fifteen and
    then recreated every one — the repair looked correct in isolation and was
    undone by the next command. It is at the end of the tenant chain for exactly
    that reason, and this is the test that says so.
    """
    _alembic(empty_db, "-n", "platform", "upgrade", "head")
    _alembic(empty_db, "upgrade", "head")

    engine = create_engine(empty_db)
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

    recreated = sorted(_INTENTIONALLY_DROPPED_INDEXES & surviving)
    assert not recreated, (
        "the tenant chain recreated indexes the platform chain dropped: "
        f"{recreated}. A revision under backend/alembic/versions/ is still "
        "creating them — the platform chain owns platform indexes now."
    )


# --- neither boot nor the tenant chain builds the platform schema ------------


def test_boot_refuses_on_a_database_with_no_platform_schema(empty_db: str) -> None:
    """``require_platform_schema`` raises, and says what to run.

    Boot used to BUILD the schema here, with ``create_all``. That is what let a
    platform table exist without the policy, trigger or CHECK that was supposed
    to come with it, so boot now checks instead — and a deployment that cannot
    serve should say why in one line rather than fail later inside a request.
    """
    engine = create_engine(empty_db)
    try:
        with pytest.raises(PlatformSchemaMissingError) as excinfo:
            require_platform_schema(engine)
    finally:
        engine.dispose()

    message = str(excinfo.value)
    # The actionable part is the whole point of raising rather than returning.
    assert "bin/migrate.py" in message
    assert "alembic -n platform upgrade head" in message


def test_bring_platform_to_head_builds_the_database_it_was_handed(empty_db: str) -> None:
    """``bring_platform_to_head(engine)`` migrates that engine's database.

    It did not, at first. ``command.upgrade`` lets env.py open its own connection
    from ``settings.database_url``, so called against any database other than the
    configured one — a scratch database in a test, most obviously — it migrated
    the configured one instead and returned success, leaving the caller's database
    untouched. Silent and in the wrong direction: the caller's next statement
    fails, and the database that did change was not the one anybody asked about.
    """
    engine = create_engine(empty_db)
    try:
        bring_platform_to_head(engine, str(_BACKEND_DIR / "alembic.ini"))

        with engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables"
                    " WHERE table_schema = 'platform' AND table_type = 'BASE TABLE'"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert tables > 15, (
        f"the platform schema of the engine passed in has {tables} tables — "
        "the chain ran somewhere else"
    )


def test_boot_is_satisfied_once_the_chain_has_run(empty_db: str) -> None:
    _alembic(empty_db, "-n", "platform", "upgrade", "head")

    engine = create_engine(empty_db)
    try:
        require_platform_schema(engine)  # must not raise
    finally:
        engine.dispose()


def test_the_tenant_chain_refuses_without_the_platform_schema(empty_db: str) -> None:
    """The tenant chain declares FKs into ``platform.users`` and creates nothing there.

    It used to be self-sufficient only because ``create_all`` sat in its env.py
    bootstrap — which is precisely how the platform schema came to be built from
    ORM metadata. The dependency is now stated, and the failure names it.

    Not ``_alembic``, which fails the test on a non-zero exit: here a non-zero
    exit is the assertion.
    """
    env = {**os.environ, "DATABASE_URL": empty_db, "DATABASE_BACKEND": "postgres"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0, (
        "the tenant chain ran to completion against a database with no platform "
        "schema, which means something is building it again"
    )
    combined = result.stdout + result.stderr
    assert "PlatformSchemaMissingError" in combined, (
        f"failed, but not with the check that explains why:\n{combined[-2000:]}"
    )
