# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL database engine, session factory, and tenant schema management.

Schema-per-practice multi-tenancy: each practice gets its own PostgreSQL schema
(practice_{id}) for HIPAA-grade data isolation. The `platform` schema stores
cross-practice data such as the practice registry and system config.

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
import urllib.parse
from contextvars import ContextVar, Token
from functools import lru_cache

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..settings import Settings, get_settings

_VALID_SCHEMA_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# Request-scoped database session, set by DatabaseSessionMiddleware
_request_session: ContextVar[Session | None] = ContextVar("_request_session", default=None)

# Sessions that WERE published on ``_request_session`` and have since been
# displaced by a nested publication (``publish_request_session``), outermost
# first. They are still open and still owned by their creator — the middleware
# closes its own session at request teardown — but nothing points at them for
# the duration, which is precisely why they need tracking: a session no code can
# reach is a session no guard can check, and an unreachable session holding an
# open transaction is the leak this whole module exists to prevent.
_displaced_sessions: ContextVar[tuple[Session, ...]] = ContextVar("_displaced_sessions", default=())

# Opaque handle tying a publication to its matching restore.
_RequestSessionBinding = tuple[Token[Session | None], Token[tuple[Session, ...]]]

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

# Request-scoped patient id, the patient analog of ``_current_user_id``
# above. Patient-facing surfaces authenticate a *patient* principal (see
# ``app.auth.patient_context``), not a clinician, so patient-scoped RLS
# policies read a separate GUC — ``app.current_patient_id``. Two GUCs
# rather than one "whoever is calling" GUC because a policy has to be
# able to say which kind of principal it grants to: a shared GUC would
# make ``USING (patient_id = current_setting(...))`` accept a clinician
# whose user id happened to equal a patient id, and would leave every
# existing clinician policy silently satisfiable by a patient principal.
_current_patient_id: ContextVar[str | None] = ContextVar("_current_patient_id", default=None)

# Key under which the request's patient id is stashed on ``Session.info``.
# Same reasoning as ``_RLS_USER_ID_KEY``: the Session object survives the
# threadpool-worker hop that discards a sync dependency's ContextVar set.
_RLS_PATIENT_ID_KEY = "rls_current_patient_id"

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

    When ``db_use_cloud_sql_connector=true`` the engine is built with a
    ``creator`` callable that opens connections via the Cloud SQL Python
    connector (``google-cloud-sql-connector[pg8000]`` or
    ``cloud-sql-python-connector`` package).  The connector handles IAM
    auth, certificate rotation, and private-IP routing transparently. The
    library is imported lazily so deployments that don't set the flag are
    completely unaffected — the package need not be installed.
    """
    settings = get_settings()

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

    if settings.db_use_cloud_sql_connector:
        return _build_cloud_sql_engine(settings, option_parts)

    # Default path: plain DSN via DATABASE_URL.
    if not settings.database_url:
        msg = "DATABASE_URL is required when database_backend=postgres"
        raise ValueError(msg)
    connect_args: dict[str, object] = {
        # Abort a hung connect (cold start, or replacing a dropped connection)
        # instead of stalling a request for minutes on an unreachable server.
        "connect_timeout": settings.database_connect_timeout_seconds,
    }
    if settings.database_tcp_keepalives_idle_seconds > 0:
        # Keep otherwise-idle connections alive so the network path
        # (NAT/LB/firewall) doesn't silently drop them -- the usual cause of
        # "SSL connection has been closed unexpectedly". pool_pre_ping and
        # pool_recycle handle whatever still slips through.
        connect_args["keepalives"] = 1
        connect_args["keepalives_idle"] = settings.database_tcp_keepalives_idle_seconds
        connect_args["keepalives_interval"] = 10
        connect_args["keepalives_count"] = 5
    if option_parts:
        connect_args["options"] = " ".join(option_parts)
    return create_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,
        pool_recycle=settings.database_pool_recycle_seconds,
        echo=settings.debug,
        connect_args=connect_args,
    )


def _build_cloud_sql_engine(settings: Settings, option_parts: list[str]) -> Engine:
    """Build a SQLAlchemy engine that connects via the Cloud SQL Python connector.

    Imported lazily (called only when ``db_use_cloud_sql_connector=true``) so
    the ``cloud-sql-python-connector`` package is not required for plain-Postgres
    deployments.

    The connector manages the mTLS handshake and certificate rotation. We use
    psycopg2 as the driver to match the existing default path.  IAM database
    auth is supported via ``enable_iam_auth=True``; in that mode the connector
    obtains a short-lived OAuth2 token and no password is needed.

    The per-connection Postgres GUC options (lock_timeout, statement_timeout,
    idle_in_transaction_session_timeout) are passed through ``options`` in the
    same way as the plain-DSN path.
    """
    try:
        from google.cloud.sql.connector import Connector, IPTypes  # type: ignore[import-untyped]
    except ImportError as exc:
        msg = (
            "db_use_cloud_sql_connector=true but the 'cloud-sql-python-connector' package "
            "is not installed. Install it with: pip install cloud-sql-python-connector "
            "or poetry install --with cloudsql"
        )
        raise ImportError(msg) from exc

    if not settings.cloud_sql_instance_connection_name:
        msg = (
            "cloud_sql_instance_connection_name is required when "
            "db_use_cloud_sql_connector=true (format: PROJECT:REGION:INSTANCE)"
        )
        raise ValueError(msg)

    try:
        ip_type = IPTypes[settings.cloud_sql_ip_type.upper()]
    except KeyError:
        msg = (
            f"Invalid cloud_sql_ip_type '{settings.cloud_sql_ip_type}'. "
            f"Valid values: PRIVATE, PUBLIC, PSC"
        )
        raise ValueError(msg)  # noqa: B904

    # Parse DB name, user, and password from DATABASE_URL so we have a single
    # source of truth.  The DSN is still required (for the database name and
    # user); the connector replaces only the transport layer.
    if not settings.database_url:
        msg = (
            "DATABASE_URL is required even when db_use_cloud_sql_connector=true "
            "(it provides the database name, user, and — unless db_iam_auth=true — password). "
            "Format: postgresql://user:pass@localhost/dbname"
        )
        raise ValueError(msg)

    parsed = urllib.parse.urlparse(settings.database_url)
    db_name = (parsed.path or "/").lstrip("/")
    db_user = parsed.username or ""
    db_pass = urllib.parse.unquote(parsed.password or "")

    connector = Connector(ip_type=ip_type)
    instance_name = settings.cloud_sql_instance_connection_name
    use_iam_auth = settings.db_iam_auth
    connect_options = " ".join(option_parts)

    def _creator() -> object:
        kwargs: dict[str, object] = {
            "dbname": db_name,
            "user": db_user,
        }
        if use_iam_auth:
            kwargs["enable_iam_auth"] = True
        else:
            kwargs["password"] = db_pass
        if connect_options:
            kwargs["options"] = connect_options
        return connector.connect(instance_name, "psycopg2", **kwargs)

    return create_engine(
        "postgresql+psycopg2://",
        creator=_creator,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,
        pool_recycle=settings.database_pool_recycle_seconds,
        echo=settings.debug,
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


def bound_db_sessions() -> tuple[Session, ...]:
    """Every session bound to this context: displaced ones first, then the current.

    ``_request_session`` names only the INNERMOST session. A caller that opened
    its own session and published it (``publish_request_session``, which is what
    ``tenant_db_session`` does) leaves the previous one open behind it, and any
    check that reads only the ContextVar is blind to exactly that session. Both
    the release helper and the guard below iterate this instead.
    """
    current = _request_session.get()
    displaced = _displaced_sessions.get()
    return (*displaced, current) if current is not None else displaced


def publish_request_session(session: Session) -> _RequestSessionBinding:
    """Publish *session* as the request-scoped session, releasing the one it displaces.

    Returns an opaque binding to hand back to :func:`restore_request_session`.

    The displaced session is COMMITTED, not merely remembered. Nothing will
    reach it again until the code that opened it tears it down, so any
    transaction it is holding sits idle for however long the inner unit of work
    takes -- and Postgres's ``idle_in_transaction_session_timeout`` terminates
    the backend underneath it. The teardown commit then fails on a closed socket
    long after the real work succeeded, turning a request that did everything
    right into a 5xx (and, on a task-queue target, into a retry of work that is
    already done). Committing here returns the connection to the pool, so the
    teardown commit finds no open transaction and needs no connection at all.

    Semantics worth stating plainly: pending work on the displaced session is
    committed rather than rolled back, which is what an explicit
    ``release_db_connection()`` at the same point would do. If the surrounding
    request later raises, that work stays committed. The alternative is not
    "the work rolls back" -- it is a connection the database kills mid-flight,
    which loses the work anyway and misreports the outcome.

    The caller must not be racing the displaced session's owner. In practice it
    never is: a nested unit of work runs while its caller is blocked waiting for
    it.
    """
    outgoing = _request_session.get()
    displaced = _displaced_sessions.get()
    if outgoing is not None:
        if outgoing.in_transaction():
            outgoing.commit()
        displaced = (*displaced, outgoing)
    return (_request_session.set(session), _displaced_sessions.set(displaced))


def restore_request_session(binding: _RequestSessionBinding) -> None:
    """Undo a :func:`publish_request_session`, re-exposing the session it displaced."""
    session_token, displaced_token = binding
    _request_session.reset(session_token)
    _displaced_sessions.reset(displaced_token)


def release_db_connection() -> None:
    """Commit the request-scoped transaction to release its pooled connection.

    Call this at a seam right before a long external call (notably the
    multi-second LLM request in the note import/generation paths) so we
    don't hold a pooled connection -- and an open transaction with
    whatever locks it took at request entry -- idle across the call. The
    connection returns to the pool; the next query auto-begins a fresh
    transaction and the ``checkout`` / ``after_begin`` listeners re-apply
    ``search_path`` and the RLS ``app.current_user_id`` GUC, so tenant
    scoping survives transparently. This is what makes "just release the
    connection" safe despite per-connection tenant state.

    Releases EVERY session bound to this context, not just the published one:
    a displaced session's connection is held just as hard, and is harder to
    notice precisely because nothing points at it.

    No-ops when no session is bound (``to_thread`` workers, CLI scripts, unit
    tests with in-memory fakes) -- there's no request-scoped transaction to
    release there.
    """
    for session in bound_db_sessions():
        session.commit()


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


def assert_no_held_db_connection(context: str = "") -> None:
    """Guard against holding a pooled DB connection across a slow external call.

    Call this at the entry of any external round-trip that can take many
    hundreds of milliseconds or more -- notably an LLM request -- so a pooled
    connection (and the open transaction holding whatever locks it took at
    request entry) is never left idle across the call. The caller is expected
    to have run ``release_db_connection()`` first; the next query after that
    auto-begins a fresh, re-armed transaction.

    Checks EVERY session bound to this context, not only the published one. A
    caller that opens its own session and publishes it displaces the previous
    one, which stays open with nothing pointing at it -- and a check that reads
    only ``_request_session`` cannot see the very session most likely to be
    stranded. Reading only the ContextVar made this guard blind in exactly the
    case it was written for.

    No-ops when no session is bound (``to_thread`` workers, CLI scripts, unit
    tests with in-memory fakes), or when no bound session has an open
    transaction -- the released, correct state. When a connection IS held:
    raises in development/test, turning a forgotten release into an immediate,
    obvious failure; in production it logs an error instead of crashing a live
    request (the call still works -- the slip is alerted, not silent).
    """
    held = sum(1 for session in bound_db_sessions() if session.in_transaction())
    if not held:
        return

    from ..settings import get_settings

    detail = (
        f"{held} pooled DB connection(s) held during an external call "
        f"({context or 'unspecified'}); call release_db_connection() before it "
        f"so a connection is not held idle across the round-trip."
    )
    if get_settings().is_production:
        import logging

        logging.getLogger(__name__).error(
            "held_db_connection_during_external_call context=%s held=%d",
            context or "unspecified",
            held,
        )
        return
    raise RuntimeError(detail)


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
    _disarm_other_principal(
        session, _RLS_PATIENT_ID_KEY, "app.current_patient_id", _current_patient_id
    )


def set_current_patient_id(patient_id: str) -> None:
    """Stash the request's patient id for transaction-local RLS.

    The patient-principal mirror of :func:`set_current_user_id`; the same
    ``after_begin`` listener re-applies it on every new transaction, and
    ``DatabaseSessionMiddleware`` clears it at request end.
    """
    _current_patient_id.set(patient_id)


def _disarm_other_principal(
    session: Session,
    info_key: str,
    guc_name: str,
    context_var: ContextVar[str | None],
) -> None:
    """Clear the principal this session is NOT running as.

    Arming one principal must un-arm the other, structurally, not by
    convention. Two reasons it cannot be left to callers:

    * The patient policies are **permissive**, so Postgres ORs them with
      the clinician policies. A transaction with both GUCs set satisfies
      both families at once and sees the UNION of clinician and patient
      grants — precisely the reach the two-principal split exists to
      prevent.
    * ``arm_current_user_id`` is called on the *request-scoped* session
      from several places outside ``get_tenant_context`` (the passkey
      route, the document-finalize and session-generation workers, the
      internal transcription routes). Any patient surface that reuses one
      of those would silently end up with both keys on one ``Session``,
      and the ``after_begin`` listener re-arms whatever it finds.

    Clearing to ``''`` rather than dropping the ``set_config`` matters:
    the GUC may already be set on this transaction, and the policies use
    the ``::text``-cast idiom where an empty GUC matches nothing.

    All three carriers have to be cleared, not just ``session.info``. The
    ``after_begin`` listener reads ``session.info`` *or* the ContextVar,
    so clearing only the first would let the next transaction re-arm the
    principal we just disarmed from the fallback.

    **The clearing statement is issued unconditionally**, and that is the
    point rather than an oversight. An earlier version skipped the
    statement when neither in-process carrier was set, reasoning that a
    transaction-local GUC cannot be armed if nothing in this process armed
    it. That reasoning holds for every call site in ``app`` today — all of
    them pass ``is_local=true`` — and it is exactly the kind of premise
    that decays: it is a claim about every present and future writer of
    these two GUCs, restated as an optimisation. A connection carrying a
    session-level ``app.current_user_id`` (``set_config(..., false)``)
    survives being returned to the pool, and the skip let that value ride
    a *patient* request all the way to the policy evaluator — both
    principals armed at once on one transaction, which is precisely the
    union-of-grants this function exists to prevent. The integration suite
    reproduces it whenever a fixture that sets a session-level GUC has
    touched the pool first.

    The skip was introduced to fix an integration-suite hang blamed on the
    extra statement holding the audit writer's transaction open. That
    diagnosis was wrong: the hang is the Cloud Logging audit dual-write
    (``audit_dual_write_enabled`` defaults to ``True``, and a developer
    machine with ADC makes a real network write inside ``_persist``), and
    it reproduces on a tree that has none of this code. Nothing here ever
    held that transaction. The cost of the statement is also smaller than
    it looks: the callers already execute a ``set_config`` of their own,
    so this adds a round trip to a transaction that is open either way.
    """
    session.info.pop(info_key, None)
    context_var.set(None)
    session.execute(text(f"SELECT set_config('{guc_name}', '', true)"))


def arm_current_patient_id(session: Session, patient_id: str) -> None:
    """Arm the RLS ``app.current_patient_id`` GUC for a patient request.

    The patient-principal mirror of :func:`arm_current_user_id`, and it
    does the same three things for the same reasons: stash the id on
    ``session.info`` so the ``after_begin`` listener can re-apply the GUC
    after a mid-request commit, stash it in the ContextVar for the
    off-request primitives, and issue ``set_config`` once for the
    transaction that is already open.

    It deliberately does **not** also arm ``app.current_user_id``. A
    patient is not a clinician with a different id: leaving the clinician
    GUC empty is what makes every existing clinician-scoped policy return
    zero rows for a patient principal, which is the fail-closed direction.
    """
    if not patient_id or not patient_id.strip():
        # An empty id would be armed as a real value — the listener guards
        # on ``is not None``, so '' reaches the GUC and ``current_setting``
        # returns '' rather than NULL. Any policy written with NULL
        # semantics would then behave differently for "no patient" than
        # intended. Refuse instead of arming a principal that is not one.
        msg = "patient_id must be a non-empty identifier"
        raise ValueError(msg)

    session.info[_RLS_PATIENT_ID_KEY] = patient_id
    set_current_patient_id(patient_id)
    session.execute(
        text("SELECT set_config('app.current_patient_id', :pid, true)"),
        {"pid": patient_id},
    )
    _disarm_other_principal(session, _RLS_USER_ID_KEY, "app.current_user_id", _current_user_id)


@event.listens_for(Session, "after_begin")
def _rearm_rls_principal_gucs_on_txn_begin(  # type: ignore[no-untyped-def]
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

    It re-applies ``app.current_patient_id`` on the same terms for a
    patient principal (see :func:`arm_current_patient_id`). One listener
    covering both GUCs rather than two: a second listener would re-arm in
    an order SQLAlchemy does not guarantee, and only one of the two is
    ever set on a given request anyway.

    **Arming one principal clears the other, on every transaction**, in
    the same statement. The transaction-local clear
    :func:`_disarm_other_principal` issues dies with the transaction that
    carried it, so a mid-request commit is exactly where a stale value
    would come back: the listener re-arms the principal that IS set and,
    without this, leaves whatever the connection is carrying for the one
    that is not. Today that can only be a session-level
    ``set_config(..., false)`` — which no ``app`` code path issues, though
    the integration suite's fixtures do, and a psql session or an ops
    script on the same connection could. The result would be both GUCs
    live on one transaction, and because the patient policies are
    PERMISSIVE Postgres ORs the two families and the request sees the
    union of clinician and patient grants. Clearing to ``''`` costs
    nothing: it rides the statement that was already being issued, and
    the ``::text``-cast idiom every policy uses treats ``''`` as matching
    no row.

    Still a no-op when neither principal is armed, so CLI scripts,
    alembic and the integration fixtures that arm a GUC themselves stay
    unaffected — this clears the *unused* principal on a transaction that
    has one, it does not impose a principal on a transaction that has
    none.

    **Both principals visible at once is refused, loudly.** The arming
    functions make it unreachable — each clears the other's carriers — but
    they clear them on the ``Session`` they are handed, and this listener
    also reads the ambient ContextVars. So a caller that armed a patient on
    the request session and then set the clinician ContextVar from the same
    context (entering ``tenant_db_session`` inline on the event loop rather
    than in a worker, against its documented contract) would present both
    here. Arming both is the union of grants, which is the one outcome this
    whole split exists to prevent, and arming neither would be a silent
    zero-row request — data vanishing mid-request, indistinguishable from
    "no data", which is the failure mode this codebase keeps getting bitten
    by. Raise instead: the state is an invariant violation with no
    legitimate producer, so a 500 naming it is strictly better than either
    guess.
    """
    user_id = session.info.get(_RLS_USER_ID_KEY) or _current_user_id.get()
    patient_id = session.info.get(_RLS_PATIENT_ID_KEY) or _current_patient_id.get()
    if user_id is None and patient_id is None:
        return

    if user_id is not None and patient_id is not None:
        # No ids in the message: both are principal identifiers, and the
        # rule is that neither reaches a log.
        msg = (
            "Both a clinician and a patient principal are armed on this "
            "session. Permissive RLS policies OR together, so this "
            "transaction would see the union of both principals' grants. "
            "Refusing to open it. Arm exactly one principal per unit of "
            "work — see arm_current_user_id / arm_current_patient_id."
        )
        raise RuntimeError(msg)

    connection.execute(
        text(
            "SELECT set_config('app.current_user_id', :uid, true), "
            "set_config('app.current_patient_id', :pid, true)"
        ),
        {"uid": user_id or "", "pid": patient_id or ""},
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

    Uses ``autocommit = True`` for the duration of the ``SET`` (mirroring
    the checkin handler) so the statement executes outside any transaction
    block.  Without this, psycopg2's default ``autocommit=False`` mode
    would start an implicit transaction the moment ``SET search_path`` runs
    — which happens very early in the request lifecycle, before the route
    handler does any real work.  If the handler then takes more than
    ``idle_in_transaction_session_timeout`` milliseconds before its first
    data query (e.g. waiting on a token verify, iCal probe, or LLM call),
    Cloud SQL terminates the connection mid-request, causing latency spikes
    or 500s as SQLAlchemy recovers.  Running the SET outside a transaction
    defers the implicit ``BEGIN`` to the handler's first genuine DB
    operation, shrinking the idle-in-transaction window to near zero.
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
    prior_autocommit = dbapi_conn.autocommit
    try:
        dbapi_conn.autocommit = True
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public")
        finally:
            cursor.close()
    finally:
        dbapi_conn.autocommit = prior_autocommit


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


# Tenant tables that a deployment knows are NOT row-scoped — their
# isolation boundary is the tenant schema (search_path), not a per-row
# predicate — but that carry an ``id`` column and so would otherwise be
# rejected by ``enable_rls_on_schema``'s deny-all guard. Core ships none;
# a deployment that adds its own non-row-scoped tenant tables registers
# them here (see ``register_overlay_not_row_scoped``) so the guard treats
# them exactly like core's own entries (``_CORE_NOT_ROW_SCOPED``).
_OVERLAY_NOT_ROW_SCOPED: set[str] = set()

# The tables core itself knows are not row-scoped. Named once, here, rather
# than spelled inline where it is used: it is needed in two places, and while
# it was a literal in both, adding an entry meant remembering both — the
# per-table notes below had already drifted out of step with the set they
# describe.
#   * ehr_routes — tenant-level EHR automation config keyed by ``ehr_system``,
#     with no owning user. Its sibling ``ehr_prompts`` carries none of the
#     scoping columns so it is never even considered; ehr_routes is only
#     considered because it carries an ``id``.
#   * scheduling_policy — practice-level booking policy, a singleton row with
#     no ``user_id`` / ``patient_id``.
#   * users — vestigial per-tenant table. Runtime identity lives in the
#     platform schema; nothing reads this per-tenant copy.
_CORE_NOT_ROW_SCOPED: frozenset[str] = frozenset({"ehr_routes", "scheduling_policy", "users"})


def not_row_scoped_tenant_tables() -> set[str]:
    """Every tenant table row-level security is deliberately left OFF for.

    The core entries above plus anything a deployment registered through
    ``register_overlay_not_row_scoped``. Their isolation boundary is the
    tenant schema (search_path), not a per-row predicate, so forcing RLS on
    them would leave a table with no policy — a silent deny-all under a
    NOBYPASSRLS role.

    Exposed as an accessor so that ``enable_rls_on_schema`` (which acts on
    the set) and ``rls_forced_tenant_tables`` (which reports the complement)
    read the same answer, and so anything checking the invariant from
    outside can ask rather than re-derive.
    """
    return set(_CORE_NOT_ROW_SCOPED) | _OVERLAY_NOT_ROW_SCOPED


def register_overlay_not_row_scoped(*table_names: str) -> None:
    """Register additional tenant tables as not-row-scoped.

    ``enable_rls_on_schema`` force-enables row-level security on every
    tenant table carrying a ``user_id`` / ``patient_id`` / ``id`` column
    and refuses (raises) to leave such a table with no policy, since that
    would be a silent deny-all. A deployment that introduces its own
    tenant table whose isolation boundary is the tenant schema rather
    than a per-row predicate (e.g. tenant-level config with only an
    ``id``) registers the table name here. Registered tables then take
    the same code path as core's own entries
    (``_CORE_NOT_ROW_SCOPED``): RLS is left disabled rather than the
    guard raising.

    Core itself registers none — the default registry is empty. This is
    purely a hook for deployment-specific tenant tables.
    """
    _OVERLAY_NOT_ROW_SCOPED.update(table_names)


# Tenant tables the PATIENT principal may reach, mapped to the column that
# identifies which patient owns the row. A registry rather than column
# inference on purpose: plenty of tables carry a ``patient_id`` without the
# patient being entitled to read them (``notes`` is the clinician's
# clinical record ABOUT the patient, not a record FOR them). Patient-
# readability is a product decision, and no column shape implies it.
#
# Core seeds exactly one entry: a patient may read their own ``patients``
# row, keyed on ``id``. Intake submissions, companion threads and
# appointments register their own through this seam in their own changes.
#
# IMPORTANT — where these policies do and do not apply. RLS is applied
# per tenant schema, and ``enable_rls_on_schema`` deliberately returns
# early for ``DEFAULT_PRACTICE_SCHEMA``. In a single-practice deployment
# (``multi_tenancy_enabled=False``, the default) all data lives in that
# schema and carries no row policies at all — clinician or patient. So
# ``app.current_patient_id`` enforces nothing there, and patient
# isolation rests entirely on each route's own predicates. Anyone writing
# a patient route in that posture must not treat RLS as the backstop; it
# is a backstop only where per-practice schemas exist.
PATIENT_READABLE_TABLES: dict[str, str] = {"patients": "id"}

# Of those, the ones a patient may also WRITE. Deliberately empty in core,
# and read and write are separate registries so granting one never silently
# grants the other.
#
# Empty here does NOT currently mean "a patient principal can write
# nothing". ``patients`` carries ``rls_patient_insert ... FOR INSERT WITH
# CHECK (true)`` — a permissive policy that consults no GUC, added to fix
# the clinician chicken-and-egg where a brand-new patient has no
# ``patient_clinicians`` grant yet and so fails ``has_patient_access`` on
# its own first INSERT. It admits any principal subject to RLS, the
# patient one included. SELECT/UPDATE/DELETE are all closed to a patient
# (they key on ``app.current_user_id``, which a patient request leaves
# empty, and the patient's own read policy is ``FOR SELECT`` only, so it
# does not widen UPDATE's ``USING``); INSERT is the one gap. Unreachable
# while no resolver and no patient route exist, and tracked to be closed
# before the first front door lands. Do not read this registry as the
# whole answer to "what can a patient write".
PATIENT_WRITABLE_TABLES: dict[str, str] = {}


def register_overlay_patient_scoped(
    table_name: str, key_column: str = "patient_id", *, writable: bool = False
) -> None:
    """Register a tenant table as reachable by the patient principal.

    ``enable_rls_on_schema`` adds an **additive** patient policy to every
    registered table: Postgres permissive policies OR together, so the
    patient arm widens access for a patient principal without altering —
    or even touching the text of — the clinician policy beside it.

    Args:
        table_name: The tenant table.
        key_column: The column holding the owning patient's id. Defaults
            to ``patient_id``; ``patients`` itself is keyed on ``id``.
        writable: Also grant UPDATE/INSERT with a matching ``WITH CHECK``.
            Off by default, so registering a table for reading never
            silently makes it writable.

    Registration is a deployment-level statement about the product, so it
    happens at bootstrap, not per-request. A registered table missing
    ``key_column`` makes ``enable_rls_on_schema`` raise rather than ship a
    policy that silently matches nothing.
    """
    PATIENT_READABLE_TABLES[table_name] = key_column
    if writable:
        PATIENT_WRITABLE_TABLES[table_name] = key_column


def _patient_principal_predicate(key_column: str) -> str:
    """The row test for "this row belongs to the calling patient".

    Same ``::text``-cast idiom as the clinician arm: the column is a native
    ``uuid`` and the GUC is always text, so casting the column (rather than
    the GUC) means an unset GUC yields NULL and matches nothing — fail
    closed, with no ``invalid input syntax for uuid`` path an attacker
    could use to distinguish states.
    """
    return f"{key_column}::text = current_setting('app.current_patient_id', true)"


def _apply_patient_principal_policies(
    session: Session, schema_name: str, table_name: str, columns: set[str]
) -> None:
    """Add the additive patient-principal policies to a registered table.

    Called for every table before the clinician policy shape is chosen, so
    a registered table gets its patient arm regardless of which clinician
    branch handles it (several of them ``continue``).

    Unregistered tables get nothing — which is the point. A clinician-
    scoped table keyed on ``app.current_user_id`` fails closed for a
    patient principal automatically, because a patient request never arms
    that GUC. That is asserted directly in the integration suite rather
    than assumed.

    ``columns`` must be the table's FULL column set, not the scoping-column
    subset the policy-shape branches switch on. Against the subset this
    check could only ever accept ``patient_id`` or ``id`` — the two key
    columns core happens to use — and would reject every other one, so the
    first table registered on, say, ``submitted_by_patient_id`` would fail
    tenant provisioning over a column that is right there in the table.
    Worse, the unit-level registry test would stay green throughout,
    because it checks the ORM metadata, which has all the columns.
    """
    import logging

    logger = logging.getLogger(__name__)

    qualified = f"{schema_name}.{table_name}"
    key_column = PATIENT_READABLE_TABLES.get(table_name)
    if key_column is None:
        return

    if key_column not in columns:
        raise RuntimeError(
            f"enable_rls_on_schema: {qualified} is registered patient-scoped on "
            f"'{key_column}', but that column is not present (columns found: "
            f"{sorted(columns)}). Refusing to create a policy that would match "
            f"nothing — fix the registration or the table."
        )

    predicate = _patient_principal_predicate(key_column)
    session.execute(
        text(f"CREATE POLICY rls_patient_self_read ON {qualified} FOR SELECT USING ({predicate})")
    )
    logger.info("RLS (patient self-read on %s) enabled on %s", key_column, qualified)

    if table_name in PATIENT_WRITABLE_TABLES:
        session.execute(
            text(
                f"CREATE POLICY rls_patient_self_write ON {qualified} "
                f"FOR UPDATE USING ({predicate}) WITH CHECK ({predicate})"
            )
        )
        logger.info("RLS (patient self-write on %s) enabled on %s", key_column, qualified)


def enable_rls_on_schema(  # noqa: PLR0912,PLR0915 — one policy arm per tenant-table shape
    session: Session, schema_name: str
) -> None:
    """Enable Row-Level Security on every patient-scoped table in the schema.

    Two policy shapes, picked by what columns the table has:

    * **user_id column** (clinician owns the row directly — patients,
      therapy_sessions, appointments, etc.): policy matches rows where
      ``user_id::text = current_setting('app.current_user_id', true)``.
      This is the original direct-ownership shape; preserves prior
      behavior so multi-clinician sharing on these tables remains a
      follow-up. The ``::text`` cast compares the native-``uuid``
      ``user_id`` column against the always-text GUC: an unset/empty
      GUC yields NULL/'' and matches nothing (fail-closed), with no
      ``invalid input syntax for uuid`` risk that casting the GUC the
      other way would carry.
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
    ``get_tenant_context``, so they arm it directly). The
    ``compliance_documents`` table owns rows via ``uploaded_by_user_id``
    (not the literal ``user_id``), so it gets the same direct-ownership
    shape keyed on that column.

    ``audit_logs`` gets a branch of its own. It carries ``user_id``, so
    it would otherwise take the direct-ownership policy — which refuses
    every row a patient principal's own action produces, because that
    principal arms a different GUC. Its policy splits on ``actor_type``
    instead: see the branch for what each half permits.

    Two kinds of tables are deliberately NOT given a row policy:
      * Tables with none of ``user_id`` / ``patient_id`` / ``id`` (e.g.
        ehr_prompts) never reach the loop — the column query above
        doesn't select them.
      * Tables that DO carry an ``id`` but aren't owned by a single
        user — ``not_row_scoped_tenant_tables()``, which names core's
        own and folds in whatever a deployment registered through
        ``register_overlay_not_row_scoped`` (core registers none) —
        have RLS left off explicitly. Their isolation boundary is the
        tenant schema (search_path), not a per-row predicate. Forcing
        RLS on them would leave no policy = a silent deny-all under a
        NOBYPASSRLS role.

    Any other table that carries one of the scoping columns but matches
    no policy shape raises — refusing to ship a force-RLS'd table with
    no policy. That guards against a newly-added table silently
    becoming deny-all (the trap the chat_messages branch documents).

    ``FORCE ROW LEVEL SECURITY`` applies the policy even to the table
    owner (defense-in-depth for HIPAA isolation). ``current_setting``
    with ``missing_ok=true`` returns NULL when the session variable is
    unset, so any query without a tenant-context middleware that set
    ``app.current_user_id`` sees zero rows — fail-closed.

    Idempotent: DROP POLICY IF EXISTS before each CREATE so the policy
    body always tracks the current code; not_row_scoped tables DISABLE
    RLS each run to heal a schema a prior version forced it on.
    """
    import logging

    logger = logging.getLogger(__name__)

    _validate_schema_name(schema_name)
    if schema_name == DEFAULT_PRACTICE_SCHEMA:
        logger.info("Skipping RLS on template schema '%s'", schema_name)
        return

    # One query per schema; gives us {table_name: {columns...}} and lets
    # us pick the right policy shape per table.
    #
    # It fetches EVERY column and narrows in Python, rather than filtering
    # to the three scoping names in SQL. Which tables enter the loop is
    # unchanged — still "carries one of user_id / patient_id / id" — but
    # the column set each branch receives is now the table's real one.
    # ``_apply_patient_principal_policies`` checks a patient-scoped
    # registration's key column against that set, and against the narrowed
    # version it could only ever accept ``patient_id`` or ``id``: the first
    # table registered on, say, ``submitted_by_patient_id`` would have
    # failed tenant provisioning over a column that is right there in the
    # table. It also makes the deny-all guard's "columns present" message
    # say what the table actually has.
    column_rows = session.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name != 'alembic_version'"
        ),
        {"schema": schema_name},
    ).fetchall()

    scoping_columns = {"user_id", "patient_id", "id"}
    all_columns: dict[str, set[str]] = {}
    for table_name, column_name in column_rows:
        all_columns.setdefault(table_name, set()).add(column_name)
    tables: dict[str, set[str]] = {
        table_name: columns
        for table_name, columns in all_columns.items()
        if columns & scoping_columns
    }

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
    # Tables that are NOT owned by a single user (core's own, plus any a
    # deployment registered) — see ``not_row_scoped_tenant_tables`` for
    # which and why. They still get caught by the column query above
    # because they carry an ``id``, but forcing RLS on them leaves no
    # policy to create — a silent deny-all under a NOBYPASSRLS role. Leave
    # RLS off explicitly, and DISABLE idempotently to heal any schema a
    # prior run forced it on.
    not_row_scoped = not_row_scoped_tenant_tables()

    # Which patient-scoped registrations actually got a policy, checked
    # against the registry after the loop. The loop only iterates tables
    # the column query returned, and that query filters to
    # ``('user_id', 'patient_id', 'id')`` — so a registered table carrying
    # none of those three names is never visited, and its registration
    # becomes a silent no-op. Silent is the bad direction: the registry
    # says the patient may read the table, no policy exists to say so, and
    # under FORCE RLS the patient reads nothing. That looks like "the
    # feature is broken" from the outside and like "we shipped the policy"
    # from the registry, which is how a grant goes missing without anyone
    # noticing it was supposed to be there.
    patient_scoped_applied: set[str] = set()

    for table_name, columns in tables.items():
        qualified = f"{schema_name}.{table_name}"
        if table_name in not_row_scoped:
            session.execute(text(f"ALTER TABLE {qualified} DISABLE ROW LEVEL SECURITY"))
            logger.info("RLS intentionally not applied to %s (not row-scoped)", qualified)
            continue
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
        # The patient-principal arm. Dropped unconditionally so a table
        # that is later UNregistered sheds its patient policy on the next
        # run, rather than keeping a stale grant nobody is looking for.
        session.execute(text(f"DROP POLICY IF EXISTS rls_patient_self_read ON {qualified}"))
        session.execute(text(f"DROP POLICY IF EXISTS rls_patient_self_write ON {qualified}"))

        # Additive: created before the clinician shape is chosen, because
        # several of those branches ``continue``. Permissive policies OR
        # together, so this widens access for a patient principal without
        # touching any clinician policy.
        _apply_patient_principal_policies(session, schema_name, table_name, columns)
        patient_scoped_applied.add(table_name)

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
            # Non-restricted categories (chart, consent) → patient_access
            # (co-treaters share). category IN ('therapist_private',
            # 'psychotherapy_notes') → uploader-only. Both restricted
            # categories collapse to the same access predicate; the
            # distinction matters at the disclosure-workflow layer, not
            # RLS. Gated by the restricted set (not an enumerated
            # allow-list) so adding a future non-restricted category
            # needs no RLS change.
            session.execute(
                text(
                    f"CREATE POLICY rls_patient_doc_access ON {qualified} "
                    f"USING ("
                    f"  (category NOT IN ('therapist_private', 'psychotherapy_notes') "
                    f"   AND has_patient_access("
                    f"    patient_id, current_setting('app.current_user_id', true)"
                    f"  )) "
                    f"  OR "
                    f"  (category IN ('therapist_private', 'psychotherapy_notes') "
                    f"   AND user_id::text = current_setting('app.current_user_id', true))"
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
                    f"USING (user_id::text = current_setting('app.current_user_id', true)) "
                    f"WITH CHECK (user_id::text = current_setting('app.current_user_id', true))"
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
        elif table_name == "audit_logs":
            # audit_logs carries user_id, so without this branch it would
            # take the generic clinician policy below — and that policy
            # compares against ``app.current_user_id``, which a patient
            # principal never arms. Every audit row a patient's own action
            # produced would be refused at INSERT, which under
            # § 164.312(b) is the one failure this table cannot have: the
            # action still happens, and nothing records it.
            #
            # So the policy splits by who acted. Anything that is not a
            # patient keeps exactly the predicate it has today, which is
            # what keeps clinician rows, anonymous public-booking rows and
            # system rows writing unchanged — they are all scoped to a
            # user_id and arm the user GUC. A patient actor is checked
            # against the patient GUC instead, and against their own id,
            # so one patient cannot write a row in another's name.
            insert_check = (
                "(actor_type IS DISTINCT FROM 'patient' AND "
                "user_id::text = current_setting('app.current_user_id', true)) OR "
                "(actor_type = 'patient' AND "
                "user_id::text = current_setting('app.current_patient_id', true))"
            )
            # Reads are unchanged: an actor reads the rows they are the
            # actor of, and nothing else. A clinician therefore does NOT
            # read a patient's audit records, and a patient principal reads
            # nothing at all — patient-actor rows are written for
            # accountability and read by no one here.
            #
            # That is deliberate. The audit log is a compliance record, not
            # a read-surface for observing what a patient did. When a
            # clinically meaningful signal matters — that an intake packet
            # was completed, and when — it belongs in a domain column that
            # says so, queried like any other clinical fact. Deriving it
            # from audit rows would make the compliance record load-bearing
            # for product behaviour, and quietly turn "who accessed what"
            # into a feed.
            select_using = "user_id::text = current_setting('app.current_user_id', true)"
            # One ALL policy, the same shape the generic branch uses, so
            # UPDATE and DELETE keep a predicate. Splitting this into
            # SELECT-only and INSERT-only policies would leave those two
            # commands with no policy at all — under RLS that is not a
            # refusal, it is a silent match against zero rows, which would
            # turn a tampering attempt from a loud trigger error into a
            # quiet no-op and would strand the retention purge.
            # The retention purge is not a principal and has no identity to
            # arm: ``_delete_expired`` sets ``search_path``, arms
            # ``app.allow_audit_purge`` and issues one DELETE across every
            # actor's rows. Under the actor policy alone that DELETE compares
            # ``user_id`` against an unset GUC and matches nothing — silently,
            # rowcount 0, a cron reporting a successful run having purged
            # nothing while rows outlive ``expires_at`` forever.
            #
            # So the purge gets its own permissive policies, gated on the same
            # GUC the append-only trigger already treats as the authorization
            # to delete. No new trust boundary: anything that can set that GUC
            # can already get a DELETE past the trigger, and RLS was only
            # hiding the rows by accident.
            #
            # SELECT as well as DELETE, because ``_count_expired`` backs
            # ``--dry-run`` and reads through the same predicate. Without it an
            # operator asking whether the purge has work is told "none" while
            # the real run deletes thousands — a worse failure than both
            # reporting zero. Both are gated on the purge GUC, so an ordinary
            # request (which never sets it) sees exactly what it saw before.
            #
            # Deliberately NOT ``FOR ALL``: that would also admit INSERT and
            # UPDATE under the purge GUC, letting a purge-context session
            # forge or rewrite rows. The trigger blocks UPDATE, but the policy
            # should not be the thing relying on it.
            purge_armed = "current_setting('app.allow_audit_purge', true) = 'on'"
            session.execute(text(f"DROP POLICY IF EXISTS rls_user_isolation ON {qualified}"))
            session.execute(text(f"DROP POLICY IF EXISTS rls_audit_actor_access ON {qualified}"))
            session.execute(text(f"DROP POLICY IF EXISTS rls_audit_purge_delete ON {qualified}"))
            session.execute(text(f"DROP POLICY IF EXISTS rls_audit_purge_select ON {qualified}"))
            session.execute(
                text(
                    f"CREATE POLICY rls_audit_actor_access ON {qualified} "
                    f"USING ({select_using}) WITH CHECK ({insert_check})"
                )
            )
            session.execute(
                text(
                    f"CREATE POLICY rls_audit_purge_delete ON {qualified} "
                    f"FOR DELETE USING ({purge_armed})"
                )
            )
            session.execute(
                text(
                    f"CREATE POLICY rls_audit_purge_select ON {qualified} "
                    f"FOR SELECT USING ({purge_armed})"
                )
            )
            logger.info("RLS (audit actor access + retention purge) enabled on %s", qualified)
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
                    f"USING (user_id::text = current_setting('app.current_user_id', true)) "
                    f"WITH CHECK (user_id::text = current_setting('app.current_user_id', true))"
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
        elif table_name == "compliance_documents":
            # compliance_documents owns rows directly via
            # ``uploaded_by_user_id`` — but the column isn't literally
            # named ``user_id``, so the user_id branch above misses it
            # and the table would otherwise fall through to a deny-all.
            # Gate by the uploader rather than the parent
            # ``compliance_item_id`` (which is nullable, so a parent-based
            # policy would hide item-less documents); uploaded_by_user_id
            # is NOT NULL, so every row is attributable.
            owner = "uploaded_by_user_id::text = current_setting('app.current_user_id', true)"
            session.execute(
                text(
                    f"CREATE POLICY rls_user_isolation ON {qualified} "
                    f"USING ({owner}) WITH CHECK ({owner})"
                )
            )
            logger.info("RLS (uploaded_by_user_id) enabled on %s", qualified)
        else:
            # We force-enabled RLS at the top of the loop but matched no
            # policy shape. Leaving it here would be a silent deny-all
            # (the exact trap the chat_messages branch calls out). Fail
            # loud so a newly-added tenant table can't ship deny-all:
            # the author must add a policy branch above, or list the
            # table in ``not_row_scoped`` if it isn't row-owned.
            raise RuntimeError(
                f"enable_rls_on_schema: no RLS policy defined for {qualified} "
                f"(columns present: {sorted(columns)}). Add a policy branch "
                f"or list the table in ``not_row_scoped`` — refusing to leave "
                f"it deny-all."
            )

    # Every patient-scoped registration that names a table in THIS schema
    # must have been given a policy above. See ``patient_scoped_applied``
    # for why a registration can otherwise be skipped without a sound.
    # Tables absent from the schema are not an error: the registry is
    # process-wide while a schema may predate the migration that adds the
    # table, and ``ensure_schemas`` reconciles those on the next run.
    registered_here = {
        table_name
        for table_name in PATIENT_READABLE_TABLES
        if table_name in tables and table_name not in not_row_scoped
    }
    skipped = registered_here - patient_scoped_applied
    unreachable = {
        table_name for table_name in PATIENT_READABLE_TABLES if table_name in not_row_scoped
    }
    if skipped or unreachable:
        raise RuntimeError(
            f"enable_rls_on_schema: patient-scoped registrations got no policy in "
            f"{schema_name} — skipped={sorted(skipped)}, "
            f"registered-but-not-row-scoped={sorted(unreachable)}. A table listed in "
            f"PATIENT_READABLE_TABLES must be row-scoped and must carry one of the "
            f"columns the schema query selects. Refusing to report success on a "
            f"grant that was never created."
        )

    session.commit()


def rls_forced_tenant_tables() -> set[str]:
    """Return every tenant table that enable_rls_on_schema force-enables RLS on.

    Mirrors that function's selection: a table is RLS-forced if it carries any
    of user_id / patient_id / id (the columns the provisioning query keys on),
    excluding alembic_version and the not_row_scoped tables (see
    ``not_row_scoped_tenant_tables``) whose isolation boundary is the schema, not
    a per-row policy. Derived from the ORM so a new RLS-bearing table — whether
    patient-access, user-owned, or special-cased — is covered automatically,
    with no hand-maintained list. MUST stay consistent with enable_rls_on_schema.
    """
    from app.db.models import Base  # lazy import — avoid circular import

    not_row_scoped = not_row_scoped_tenant_tables()
    scoping_cols = {"user_id", "patient_id", "id"}
    result: set[str] = set()
    for table_name, table in Base.metadata.tables.items():
        if table_name == "alembic_version" or table_name in not_row_scoped:
            continue
        if scoping_cols & {c.name for c in table.columns}:
            result.add(table_name)
    return result


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
    "register_overlay_not_row_scoped",
    "rls_forced_tenant_tables",
    "run_in_tenant",
    "tenant_db_session",
]
