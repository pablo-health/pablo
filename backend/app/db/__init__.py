# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL database engine, session factory, and tenant schema management.

Schema-per-practice multi-tenancy: each practice gets its own PostgreSQL schema
(practice_{id}) for HIPAA-grade data isolation. The `platform` schema stores
cross-practice data (practice registry, subscriptions, phone numbers).

Usage:
    from app.db import get_db_session, get_engine

    session = get_db_session()  # gets the request-scoped session from contextvar

Off-request tenant context (background tasks, workers):

    from app.db import run_in_tenant, tenant_db_session

    # Async helper — opens the session inside the worker thread:
    result = await run_in_tenant(schema, user_id, my_sync_fn)

    # Sync context manager — use inside a worker (not on the event loop):
    with tenant_db_session(schema, user_id) as session:
        ...
"""

import re
from contextvars import ContextVar
from functools import lru_cache

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..settings import get_settings

_VALID_SCHEMA_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# Request-scoped database session, set by DatabaseSessionMiddleware
_request_session: ContextVar[Session | None] = ContextVar("_request_session", default=None)

# Request-scoped tenant schema name. Propagates to the pool-checkout
# event listener so every connection grabbed during a request — including
# from spawned response-body tasks — re-applies the caller's search_path.
# A no-op (None) outside request scope, where callers (CLI, alembic,
# standalone sessions) set search_path explicitly.
_current_tenant_schema: ContextVar[str | None] = ContextVar("_current_tenant_schema", default=None)

# Request-scoped clinician user id, used to re-arm the transaction-local
# ``app.current_user_id`` GUC on every fresh transaction. The GUC is set
# ``is_local=true`` (xact-scoped) so connection-pool reuse can't leak a
# previous request's user identity across requests — but that means a
# mid-request commit (THERAPY-da7t's lock-release pattern) clears the
# GUC and the next transaction's queries would otherwise see an empty
# value and silently return zero rows under RLS. The ``after_begin``
# event listener below re-applies the GUC at the start of every new
# transaction from the user id armed on ``Session.info`` (with this
# ContextVar as a fallback), so explicit commits in service code are
# safe — including on sync routes, where this ContextVar would be lost
# across threadpool workers (see ``arm_current_user_id``).
_current_user_id: ContextVar[str | None] = ContextVar("_current_user_id", default=None)

# Key under which the request's clinician user id is stashed on
# ``Session.info``. The ``after_begin`` listener prefers this over the
# ContextVar because the Session object is shared by reference across the
# threadpool workers that run a sync route's dependencies and endpoint,
# whereas a ContextVar set inside a sync dependency is discarded with that
# worker thread (see :func:`arm_current_user_id`).
_RLS_USER_ID_KEY = "rls_current_user_id"

# Default practice schema for Pablo Solo (single practice)
DEFAULT_PRACTICE_SCHEMA = "practice"
PLATFORM_SCHEMA = "platform"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Create and cache the SQLAlchemy engine.

    Pool sizing and per-connection timeouts are env-driven so each
    deployment tier (self-hosted f1-micro, managed g1-small, custom-N)
    can match the database's ``max_connections`` ceiling without a code
    change. Defaults are conservative (sized for a self-hosted Postgres
    with ``max_connections=25``).

    ``connect_args.options`` sets per-connection Postgres GUCs that bound
    how long any one statement / transaction can hold locks. Defense in
    depth for the THERAPY-da7t class of bug: even if a request path
    accidentally holds locks across a slow external call, the connection
    self-heals instead of stalling the whole pool. A value of ``0`` for
    any of the three timeout settings drops that GUC from the options
    string so the server default applies.

    Pool budget rule of thumb: ``(pool_size + max_overflow) * max
    instance count`` must stay under the database's ``max_connections``
    minus ~10 reserved for migrations / admin / monitoring.
    """
    settings = get_settings()
    if not settings.database_url:
        msg = "DATABASE_URL is required when database_backend=postgres"
        raise ValueError(msg)

    option_parts: list[str] = []
    if settings.database_lock_timeout_ms > 0:
        option_parts.append(f"-c lock_timeout={settings.database_lock_timeout_ms}")
    if settings.database_idle_in_transaction_timeout_ms > 0:
        option_parts.append(
            "-c idle_in_transaction_session_timeout="
            f"{settings.database_idle_in_transaction_timeout_ms}"
        )
    if settings.database_statement_timeout_ms > 0:
        option_parts.append(f"-c statement_timeout={settings.database_statement_timeout_ms}")
    connect_args = {"options": " ".join(option_parts)} if option_parts else {}

    return create_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,
        echo=settings.debug,
        connect_args=connect_args,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Create and cache the session factory."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db_session() -> Session:
    """Get the current request-scoped database session.

    Set by DatabaseSessionMiddleware. Raises RuntimeError if called
    outside a request context (i.e., middleware hasn't run yet).
    """
    session = _request_session.get()
    if session is None:
        msg = (
            "No database session in context. "
            "Ensure DatabaseSessionMiddleware is installed and database_backend=postgres."
        )
        raise RuntimeError(msg)
    return session


def assert_tenant_schema_set() -> None:
    """Verify the session's search_path is NOT the default 'practice' schema.

    Call this before any write operation when multi_tenancy_enabled=True.
    Prevents accidental cross-tenant data leakage (HIPAA violation).
    Raises RuntimeError if the schema hasn't been switched from the default.
    """
    from ..settings import get_settings

    if not get_settings().multi_tenancy_enabled:
        return

    session = _request_session.get()
    if session is None:
        return

    result = session.execute(text("SHOW search_path"))
    search_path = result.scalar() or ""
    is_default = (
        search_path.startswith(DEFAULT_PRACTICE_SCHEMA + ",")
        or search_path == DEFAULT_PRACTICE_SCHEMA
    )
    if is_default:
        msg = (
            f"TENANT ISOLATION VIOLATION: search_path is '{search_path}' "
            f"(default schema) but multi_tenancy_enabled=True. "
            f"This would write data to the shared schema instead of the tenant's schema. "
            f"Ensure get_tenant_context() ran before this code path."
        )
        raise RuntimeError(msg)


def _validate_schema_name(name: str) -> None:
    """Validate a PostgreSQL schema name to prevent SQL injection.

    Schema names are interpolated into SET search_path statements and cannot
    use bind parameters, so we must validate the identifier strictly.
    """
    if not _VALID_SCHEMA_RE.match(name):
        raise ValueError(f"Invalid schema name: {name!r}")


def set_tenant_schema(session: Session, practice_schema: str = DEFAULT_PRACTICE_SCHEMA) -> None:
    """Set the search_path for a session to include the practice schema.

    This scopes all unqualified table references to the practice's schema,
    providing schema-level tenant isolation. Also stashes the schema name
    in ``_current_tenant_schema`` so the pool-checkout event listener can
    re-apply it on any subsequent connection grab — useful when the
    session's first connection is returned to the pool and a later
    operation (e.g. a deferred task) checks out a fresh one whose
    server-side ``search_path`` is whatever a previous user left on it.
    """
    _validate_schema_name(practice_schema)
    session.execute(text(f"SET search_path = {practice_schema}, {PLATFORM_SCHEMA}, public"))
    _current_tenant_schema.set(practice_schema)


def set_current_user_id(user_id: str) -> None:
    """Stash the request's clinician user id for transaction-local RLS.

    Pairs with the ``after_begin`` Session listener below: every new
    transaction begun on a session (including the auto-begin after an
    explicit ``session.commit()``) re-applies
    ``set_config('app.current_user_id', :uid, true)`` from this
    ContextVar. Callers in service code can therefore commit mid-
    request to release locks (THERAPY-da7t) without losing RLS context
    on the next query.

    The ContextVar is cleared by ``DatabaseSessionMiddleware`` at
    request end (alongside ``_current_tenant_schema``) so connection
    pool reuse can never leak the user id across requests.
    """
    _current_user_id.set(user_id)


def arm_current_user_id(session: Session, user_id: str) -> None:
    """Arm the RLS ``app.current_user_id`` GUC for a tenant-scoped write.

    Combines the steps every write path needs when it can't go through
    ``get_tenant_context`` (e.g. pre-MFA onboarding routes that upsert
    the caller's own tenant row):

    1. Stash the id on ``session.info`` so the ``after_begin`` listener
       can re-apply the GUC across any mid-request commit from *the
       session object itself*, not a ContextVar.
    2. Stash the id in the request ContextVar via
       :func:`set_current_user_id` too — kept for the off-request
       primitives (``tenant_db_session`` / ``run_in_tenant``) that arm
       the GUC through the ContextVar on a single worker thread.
    3. Issue ``set_config`` once for the transaction that's already open
       (the first ``BEGIN`` fires lazily on the first query, before we
       get here, so the listener won't have armed it yet).

    Why ``session.info`` and not just the ContextVar: **sync** routes run
    in FastAPI's anyio threadpool, and so do their sync dependencies.
    Each ``run_in_threadpool`` call copies the event-loop context into a
    *throwaway* worker thread, so a ``ContextVar.set()`` inside the sync
    auth dependency is discarded when that thread returns — the endpoint
    runs later in a *different* worker thread whose context copy never
    saw the set, leaving ``_current_user_id`` at ``None``. The
    request-scoped ``Session``, by contrast, is shared by reference
    across those threads (the middleware publishes it on a ContextVar
    that *does* survive, because it's set in the event-loop context), so
    a value on ``session.info`` is visible wherever that session is used.
    That's what makes the post-commit re-arm work for sync routes that
    ``_commit_intermediate`` mid-request before the SOAP LLM call.

    ``session`` must be the request-scoped session the subsequent write
    runs through, so the GUC lands on the same transaction.
    """
    session.info[_RLS_USER_ID_KEY] = user_id
    set_current_user_id(user_id)
    session.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": user_id},
    )


@event.listens_for(Session, "after_begin")
def _rearm_rls_user_id_on_txn_begin(  # type: ignore[no-untyped-def]
    session, _transaction, connection
) -> None:
    """Re-apply the RLS ``app.current_user_id`` GUC on every txn begin.

    ``set_config(name, value, is_local=true)`` is xact-scoped: commits
    (including the mid-request commits ``_commit_intermediate`` uses
    to release locks before the SOAP-generation LLM call) clear the
    GUC. Without this listener the next query in the request would see
    an empty value and RLS would silently return zero rows — fail-
    closed by design, but a regression from the prior "one transaction
    per request" model.

    The listener fires for every new transaction on every Session,
    inside the new transaction (``after_begin`` runs after the BEGIN
    has been issued). It reads the armed user id from ``session.info``
    first and falls back to the request ContextVar. ``session.info`` is
    the source of truth because it rides the Session object itself, so
    it survives across the separate threadpool workers that run a sync
    route's dependency (where the GUC is armed) and its endpoint (where
    the mid-request commit happens); a ContextVar set in the dependency's
    worker would not — see :func:`arm_current_user_id`. It no-ops when
    neither is set — CLI scripts, alembic, integration test fixtures that
    arm the GUC themselves all stay unaffected.
    """
    user_id = session.info.get(_RLS_USER_ID_KEY) or _current_user_id.get()
    if user_id is None:
        return
    connection.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": user_id},
    )


@event.listens_for(Engine, "checkout")
def _reapply_search_path_on_checkout(dbapi_conn, _conn_record, _conn_proxy) -> None:  # type: ignore[no-untyped-def]
    """Re-apply ``search_path`` from the request-scoped ContextVar on every
    pool checkout.

    Belt-and-braces alongside the explicit ``set_tenant_schema`` call the
    session middleware makes at the start of each request: PostgreSQL's
    ``SET search_path`` persists on the connection, so pool reuse can
    leak a previous request's tenant schema into a new request's first
    operation if the session hadn't issued its own ``SET`` yet. This
    listener closes that window by issuing the right ``SET`` at the
    moment the connection enters the new operation's scope.

    No-op when the ContextVar is unset (CLI scripts, alembic, standalone
    sessions created without a schema arg) — those callers manage their
    own ``search_path`` explicitly.
    """
    schema = _current_tenant_schema.get()
    if schema is None:
        return
    if not _VALID_SCHEMA_RE.match(schema):
        # Should be unreachable — set_tenant_schema validates before
        # writing the ContextVar — but if a downstream caller bypasses
        # that path and writes a bad value, refuse the SET rather than
        # interpolate untrusted input into raw SQL.
        return
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public")
    finally:
        cursor.close()


@event.listens_for(Engine, "checkin")
def _reset_search_path_on_checkin(dbapi_conn, _conn_record) -> None:  # type: ignore[no-untyped-def]
    """Return the connection to a neutral ``search_path`` on pool checkin.

    PostgreSQL's ``SET search_path`` is session-level and persists across
    transactions, so a connection that was scoped to ``practice_abc`` during
    request A will still have that search_path when it is handed to request B
    — even after the pool's ``reset_on_return='rollback'`` has cleared any
    open transaction.  A checkout that forgets to call ``set_tenant_schema``
    (background tasks, worker threads, CLI code) would then silently resolve
    unqualified table names against the *previous* request's tenant schema
    instead of failing visibly — a fail-open data-isolation hazard.

    This listener fires after the pool's own ``reset_on_return`` rollback has
    already completed (the ``checkin`` event fires after ``_reset()``, which
    issues the ROLLBACK, inside ``ConnectionPoolEntry.checkin()``).  The
    connection is post-ROLLBACK at this point, but psycopg2's default
    ``autocommit=False`` mode would start a new implicit transaction block
    on the very first SQL statement, so the implementation explicitly sets
    ``autocommit = True`` for the duration of the ``SET`` (see inline
    comments in the body below).

    ``SET search_path = public`` is used rather than ``DISCARD ALL`` for two
    reasons:
    1. ``DISCARD ALL`` also clears session-level GUCs set via
       ``connect_args={'options': '-c lock_timeout=... -c statement_timeout=...'}``
       at physical connection open time.  Those timeouts are defense-in-depth
       and must survive across pool cycles.
    2. The narrow ``SET search_path`` is sufficient: the only session state
       that this codebase legitimately stamps onto pooled connections is the
       tenant ``search_path``; everything else is either transaction-local
       (``app.current_user_id`` via ``is_local=true``) or connection-level
       GUCs that should not be cleared.

    Pair with the ``checkout`` listener above (checkin neutral → checkout
    re-stamps the correct tenant schema from the ContextVar): a connection
    sitting idle in the pool always holds a neutral path, and a checkout with
    an armed ContextVar immediately gets the right one.  A checkout without
    an armed ContextVar (background worker, forgetful caller) resolves to
    ``public`` — which has no tenant tables — so it fails closed rather than
    inheriting a prior tenant's data.

    ``public`` is safe as the neutral sentinel: it contains only
    platform-level shared objects and no patient data.  ``None`` / connection
    failure is handled by the ``dbapi_conn is None`` guard — the pool can
    pass ``None`` when the connection was invalidated.
    """
    if dbapi_conn is None:
        # Invalidated connection — nothing to reset.
        return
    # ``SET search_path`` is session-level (not transaction-local), so it
    # persists through a subsequent ROLLBACK — which is exactly what we
    # want.  However, psycopg2 in its default ``autocommit=False`` mode
    # implicitly opens a new transaction block on the very first SQL
    # statement after a ROLLBACK.  Executing ``SET search_path = public``
    # here would therefore leave the connection sitting in an open
    # (empty) transaction, which causes ``pool_pre_ping`` on the next
    # checkout to fail with "set_session cannot be used inside a
    # transaction" when it tries to set ``autocommit = True``.
    #
    # Fix: set ``autocommit = True`` for the duration of the SET so the
    # statement executes outside any transaction block, then restore the
    # prior mode.  This is safe because:
    #  * The connection is post-ROLLBACK — any tenant transaction has
    #    already been committed or rolled back by ``reset_on_return``.
    #  * ``SET search_path`` is idempotent and has no transaction
    #    semantics; it takes effect immediately regardless of autocommit.
    #  * Restoring ``autocommit = False`` leaves the connection in the
    #    mode SQLAlchemy expects when it checks it out for a new request.
    prior_autocommit = dbapi_conn.autocommit
    try:
        dbapi_conn.autocommit = True
        cursor = dbapi_conn.cursor()
        try:
            # Neutral baseline = platform, public (mirrors the no-tenant tail of
            # set_tenant_schema). Drops any tenant schema — so tenant tables are
            # unreachable and a forgetful checkout fails closed — while keeping
            # the shared platform schema (audit log, users) reachable. Resetting
            # to bare ``public`` would also hide platform tables and break every
            # platform-level write on a re-pooled connection.
            cursor.execute(f"SET search_path = {PLATFORM_SCHEMA}, public")
        finally:
            cursor.close()
    finally:
        dbapi_conn.autocommit = prior_autocommit


def create_standalone_session(practice_schema: str | None = None) -> Session:
    """Create a standalone session outside of request context.

    Useful for CLI scripts, migrations, and provisioning.
    Caller is responsible for commit/rollback/close.
    """
    session = get_session_factory()()
    if practice_schema:
        set_tenant_schema(session, practice_schema)
    return session


def enable_rls_on_schema(session: Session, schema_name: str) -> None:
    """Enable Row-Level Security on every patient-scoped table in the schema.

    Two policy shapes, picked by what columns the table has:

    * **user_id column** (clinician owns the row directly — patients,
      therapy_sessions, appointments, etc.): policy matches rows where
      ``user_id = current_setting('app.current_user_id', true)``. This
      is the original direct-ownership shape; preserves prior behavior
      so multi-clinician sharing on these tables remains a follow-up.
    * **patient_id column with no user_id** (row is owned indirectly
      via the patient — currently just ``notes``): policy matches rows
      where ``has_patient_access(patient_id, current_setting(...))``
      returns true. The function is defined by migration
      ``777b846ab944`` and looks up the ``patient_clinicians`` access
      table — supports primary, co-treating, supervisor, and coverage
      grants without further policy churn.

    The user_id shape also covers ``clinician_profiles`` — each
    clinician owns their single profile row — so writes to it require
    ``app.current_user_id`` to be armed (see ``arm_current_user_id``;
    the pre-MFA onboarding routes that upsert it can't go through
    ``get_tenant_context``, so they arm it directly). Tables with none
    of ``user_id`` / ``patient_id`` / ``id`` (e.g. audit_logs) are
    skipped; they're not row-scoped and live behind the tenant-schema
    boundary plus application-layer checks.

    ``FORCE ROW LEVEL SECURITY`` applies the policy even to the table
    owner (defense-in-depth for HIPAA isolation). ``current_setting``
    with ``missing_ok=true`` returns NULL when the session variable is
    unset, so any query without a tenant-context middleware that set
    ``app.current_user_id`` sees zero rows — fail-closed.

    Idempotent: DROP POLICY IF EXISTS before each CREATE so the policy
    body always tracks the current code.
    """
    import logging

    logger = logging.getLogger(__name__)

    _validate_schema_name(schema_name)
    if schema_name == DEFAULT_PRACTICE_SCHEMA:
        logger.info("Skipping RLS on template schema '%s'", schema_name)
        return

    # One query per schema; gives us {table_name: {columns...}} and lets
    # us pick the right policy shape per table.
    column_rows = session.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = :schema "
            "AND column_name IN ('user_id', 'patient_id', 'id') "
            "AND table_name != 'alembic_version'"
        ),
        {"schema": schema_name},
    ).fetchall()

    tables: dict[str, set[str]] = {}
    for table_name, column_name in column_rows:
        tables.setdefault(table_name, set()).add(column_name)

    if not tables:
        logger.info(
            "No tables with user_id or patient_id in schema '%s' — nothing to do",
            schema_name,
        )
        return

    # `patient_clinicians` is the access table itself — applying the
    # access-function policy to its own backing table would cause an
    # infinite recursion in the policy evaluator. Direct ownership via
    # user_id is the correct shape: only the clinician whose grant row
    # this is can see it. Other grants for the same patient are
    # invisible to peers, which matches the v1 "primary clinician owns
    # the relationship" model.
    for table_name, columns in tables.items():
        qualified = f"{schema_name}.{table_name}"
        session.execute(text(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY"))
        session.execute(text(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY"))
        session.execute(text(f"DROP POLICY IF EXISTS rls_user_isolation ON {qualified}"))
        session.execute(text(f"DROP POLICY IF EXISTS rls_patient_access ON {qualified}"))
        session.execute(text(f"DROP POLICY IF EXISTS rls_patient_doc_access ON {qualified}"))
        # Per-command policies on ``patients`` (split out from the
        # legacy single ALL policy to fix the INSERT chicken-and-egg).
        # Idempotent for tables that don't have these policies.
        session.execute(text(f"DROP POLICY IF EXISTS rls_patient_modify ON {qualified}"))
        session.execute(text(f"DROP POLICY IF EXISTS rls_patient_delete ON {qualified}"))
        session.execute(text(f"DROP POLICY IF EXISTS rls_patient_insert ON {qualified}"))

        # Pick the policy shape:
        #   * patient_documents — combined policy. Non-private rows
        #     follow patient_access (co-treaters see the chart);
        #     private rows collapse to uploader-only. Single CREATE
        #     POLICY with an OR so PG can short-circuit.
        #   * patients (the access target itself) — gate by id via the
        #     has_patient_access function.
        #   * Any other table with patient_id — gate by patient_id via
        #     has_patient_access.
        #   * Fallback to direct user_id ownership for tables that have
        #     a user_id column but no patient_id (e.g. availability_rules,
        #     google_calendar_tokens, ical_client_mappings).
        if table_name == "patient_documents":
            # category = 'chart' → patient_access (co-treaters share).
            # category IN ('therapist_private', 'psychotherapy_notes')
            # → uploader-only. Both restricted categories collapse to
            # the same access predicate; the distinction matters at
            # the disclosure-workflow layer, not RLS.
            session.execute(
                text(
                    f"CREATE POLICY rls_patient_doc_access ON {qualified} "
                    f"USING ("
                    f"  (category = 'chart' AND has_patient_access("
                    f"    patient_id, current_setting('app.current_user_id', true)"
                    f"  )) "
                    f"  OR "
                    f"  (category IN ('therapist_private', 'psychotherapy_notes') "
                    f"   AND user_id = current_setting('app.current_user_id', true))"
                    f")"
                )
            )
            logger.info(
                "RLS (patient_doc_access: chart=patient_access, restricted=uploader) enabled on %s",
                qualified,
            )
            continue
        if table_name == "patient_clinicians":
            # The grant table itself: gating it via
            # ``has_patient_access(patient_id, …)`` would recurse
            # (the function reads patient_clinicians), and even a
            # non-recursive check on ``patient_id`` would create the
            # same INSERT chicken-and-egg as ``patients`` had — a
            # new grant's row can't satisfy "has a grant for this
            # patient" until that very grant exists. Gate by
            # ``user_id`` directly: each clinician sees only their
            # own grants, and INSERTs are permitted as long as the
            # row's ``user_id`` matches the current user (i.e. you
            # can't grant access to other clinicians from a normal
            # request path — admin endpoints would run with a
            # different effective user via SET ROLE or similar).
            session.execute(
                text(
                    f"CREATE POLICY rls_user_isolation ON {qualified} "
                    f"USING (user_id = current_setting('app.current_user_id', true)) "
                    f"WITH CHECK (user_id = current_setting('app.current_user_id', true))"
                )
            )
            logger.info("RLS (user_id) enabled on %s", qualified)
        elif table_name == "patients":
            # The ``patients`` table is the access-table target: a row's
            # grant in ``patient_clinicians`` is what makes the row
            # visible. Letting USING also gate INSERT (PG's default
            # when WITH CHECK is omitted) creates a chicken-and-egg —
            # ``has_patient_access(new_id, user)`` returns false for
            # a brand-new patient because the grant doesn't exist yet,
            # so the very first INSERT into a fresh tenant is rejected
            # ("new row violates row-level security policy"). This is
            # exactly the failure mode the 2026-05-17 pentest hit on
            # freshly-provisioned ``practice_pentest_*`` schemas.
            #
            # Split the policy: USING gates SELECT/UPDATE/DELETE
            # (only granted clinicians can read or modify rows), and
            # an explicit ``WITH CHECK (true)`` permits INSERTs. The
            # app inserts the patient + its primary-clinician grant
            # in the same flush so a new patient is immediately
            # visible to its creator. Other tables (sessions, notes,
            # etc.) keep the USING-as-WITH-CHECK shape — they SHOULD
            # require an existing grant to insert.
            session.execute(
                text(
                    f"CREATE POLICY rls_patient_access ON {qualified} "
                    f"FOR SELECT USING (has_patient_access("
                    f"  id, current_setting('app.current_user_id', true)"
                    f"))"
                )
            )
            session.execute(
                text(
                    f"CREATE POLICY rls_patient_modify ON {qualified} "
                    f"FOR UPDATE USING (has_patient_access("
                    f"  id, current_setting('app.current_user_id', true)"
                    f"))"
                )
            )
            session.execute(
                text(
                    f"CREATE POLICY rls_patient_delete ON {qualified} "
                    f"FOR DELETE USING (has_patient_access("
                    f"  id, current_setting('app.current_user_id', true)"
                    f"))"
                )
            )
            session.execute(
                text(
                    f"CREATE POLICY rls_patient_insert ON {qualified} FOR INSERT WITH CHECK (true)"
                )
            )
            logger.info("RLS (patient_access split policies) enabled on %s", qualified)
        elif "user_id" in columns:
            # Tables where a row has a direct owning clinician
            # (therapy_sessions, appointments, audit_logs, etc.).
            # Each user sees only their own rows. WITH CHECK matches
            # USING explicitly so INSERTs are permitted as long as the
            # new row's ``user_id`` matches the current user — no
            # chicken-and-egg, and you can't insert a row claiming
            # someone else's user_id from a normal request path.
            #
            # ``patient_id`` is checked AFTER ``user_id`` (despite
            # both columns sometimes coexisting on the same table —
            # e.g. ``therapy_sessions``, ``audit_logs``) because the
            # documented intent is "user owns the row directly", and
            # the patient_id-based policy is only meant for tables
            # that have *no* user_id column (currently just
            # ``notes``).
            session.execute(
                text(
                    f"CREATE POLICY rls_user_isolation ON {qualified} "
                    f"USING (user_id = current_setting('app.current_user_id', true)) "
                    f"WITH CHECK (user_id = current_setting('app.current_user_id', true))"
                )
            )
            logger.info("RLS (user_id) enabled on %s", qualified)
        elif "patient_id" in columns:
            session.execute(
                text(
                    f"CREATE POLICY rls_patient_access ON {qualified} "
                    f"USING (has_patient_access("
                    f"  patient_id, "
                    f"  current_setting('app.current_user_id', true)"
                    f"))"
                )
            )
            logger.info("RLS (patient_access) enabled on %s", qualified)
        elif table_name == "chat_messages":
            # chat_messages has neither user_id nor patient_id — gate
            # by the parent conversation's patient. Without this branch
            # the loop ENABLE+FORCEs RLS but leaves no policy, which is
            # a deny-all configuration that only the BYPASSRLS-on-role
            # posture mistakenly hides in production.
            session.execute(text(f"DROP POLICY IF EXISTS rls_chat_message_access ON {qualified}"))
            # ``qualified`` + ``schema_name`` are validated identifiers
            # built from the validated schema name (see _validate_schema_name
            # earlier) and a fixed table name; not user input.
            session.execute(
                text(
                    f"CREATE POLICY rls_chat_message_access ON {qualified} "  # noqa: S608
                    f"USING (EXISTS ("
                    f"  SELECT 1 FROM {schema_name}.chat_conversations c "
                    f"  WHERE c.id = {qualified}.conversation_id "
                    f"    AND has_patient_access("
                    f"      c.patient_id, "
                    f"      current_setting('app.current_user_id', true)"
                    f"    )"
                    f"))"
                )
            )
            logger.info(
                "RLS (chat_message_access via parent conversation) enabled on %s",
                qualified,
            )

    session.commit()


def enable_rls_on_all_practice_schemas(engine: Engine | None = None) -> None:
    """Apply RLS to every existing practice_* schema (excluding the template).

    Does NOT run automatically — call from a migration script or management command.
    Skips the base 'practice' template schema and the 'platform' schema.
    """
    import logging

    logger = logging.getLogger(__name__)

    if engine is None:
        engine = get_engine()

    with Session(engine) as session:
        schemas = session.execute(
            text(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name LIKE 'practice_%'"
            )
        ).fetchall()

        for (schema_name,) in schemas:
            if schema_name == DEFAULT_PRACTICE_SCHEMA:
                continue
            logger.info("Applying RLS to schema '%s'", schema_name)
            enable_rls_on_schema(session, schema_name)


# Re-export the off-request tenant-session primitives so callers can
# import them from the package root without knowing the sub-module.
from .tenant_session import run_in_tenant, tenant_db_session  # noqa: E402

__all__ = [
    "run_in_tenant",
    "tenant_db_session",
]
