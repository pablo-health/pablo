# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tenant-scoped database session primitives for off-request work.

``DatabaseSessionMiddleware`` sets up a tenant-scoped SQLAlchemy session for
every HTTP request.  Work that runs outside a request scope — background tasks,
scheduled jobs, ``asyncio.to_thread`` workers — has no such session, and the
three components of tenant context are all missing:

* ``search_path`` GUC — scopes unqualified table names to the practice schema.
* ``app.current_user_id`` GUC — arms row-level security policies.
* ``_request_session`` ContextVar — the knob the repo factories pull to get
  the current session.

This module provides two public helpers that replicate the same lifecycle:

``tenant_db_session(schema, user_id)``
    Synchronous context manager.  Opens a standalone session, applies the
    search_path, arms RLS, publishes the session on ``_request_session`` so
    existing repo factories resolve it, and commit/rollback/closes on exit.
    Must be entered on the same thread that will run the DB work — sessions
    are not thread-safe.

``run_in_tenant(schema, user_id, fn, *args, **kwargs)``
    Async helper.  Runs a sync callable inside ``tenant_db_session`` **on a
    worker thread** via ``asyncio.to_thread``.  This is the safe pattern for
    background work that needs a tenant DB session: the context manager is
    entered inside the worker, so the session never crosses a thread boundary,
    and the ContextVars are properly propagated via the copy ``to_thread``
    makes.

Typical usage (background task, e.g. a post-request audit write)::

    from app.db.tenant_session import run_in_tenant

    async def _background_emit_audit(schema: str, user_id: str) -> None:
        def _sync(session):
            # session is already tenant-scoped and RLS-armed
            ...

        await run_in_tenant(schema, user_id, _sync)

    # Or directly with the context manager on a worker thread:
    from app.db.tenant_session import tenant_db_session

    def _worker(schema: str, user_id: str) -> None:
        with tenant_db_session(schema, user_id) as session:
            ...

    await asyncio.to_thread(_worker, schema, user_id)

The fail-closed safety net (``assert_tenant_schema_set``) blocks any session
flush to the default shared schema when multi-tenancy is enabled, matching
the guard in ``DatabaseSessionMiddleware``.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import TYPE_CHECKING, TypeVar

from sqlalchemy import text

from . import (
    _current_tenant_schema,
    _current_user_id,
    _request_session,
    assert_tenant_schema_set,
    create_standalone_session,
    set_current_user_id,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.orm import Session

__all__ = ["run_in_tenant", "tenant_db_session"]

_T = TypeVar("_T")


@contextmanager
def tenant_db_session(
    schema: str,
    user_id: str,
) -> Iterator[Session]:
    """Open a tenant-scoped DB session for one unit of off-request work.

    Mirrors the lifecycle of ``DatabaseSessionMiddleware`` +
    ``get_tenant_context``:

    1. Open a standalone session and apply ``search_path`` to *schema*
       (via ``create_standalone_session`` which calls ``set_tenant_schema``).
    2. Set the transaction-local ``app.current_user_id`` GUC so
       row-level security policies filter to this clinician's rows.
    3. Stash the same id in ``_current_user_id`` so the ``after_begin``
       Session listener re-arms the GUC after any mid-flight
       ``session.commit()`` (same pattern as the HTTP path).
    4. Publish the session on ``_request_session`` so repo factories
       calling ``get_db_session()`` resolve it without changes.
    5. On exit: commit if dirty (with fail-closed
       ``assert_tenant_schema_set`` guard); rollback on exception;
       close the session; clear both ContextVars.

    This context manager **must be entered on the thread that will run
    the DB work**.  SQLAlchemy sessions are not thread-safe.  When
    dispatching work to a thread pool, use ``run_in_tenant`` or wrap
    this manager *inside* the worker function passed to
    ``asyncio.to_thread``.

    Args:
        schema: The practice schema name (e.g. ``"practice_abc123"``).
            Must match the regex ``^[a-z][a-z0-9_]{0,62}$`` —
            ``set_tenant_schema`` validates and raises ``ValueError`` on
            bad input so this never interpolates untrusted text into SQL.
        user_id: The Pablo-internal clinician user id.  Used to arm the
            ``app.current_user_id`` GUC for RLS.

    Yields:
        The tenant-scoped ``Session``.

    Raises:
        ValueError: If *schema* fails identifier validation.
        RuntimeError: If the session is flushed with the default schema
            and multi-tenancy is enabled (fail-closed isolation guard).
    """
    # Snapshot the tenant-schema ContextVar BEFORE create_standalone_session
    # arms it: that call runs set_tenant_schema(), which writes
    # _current_tenant_schema. The Engine "checkout" listener reads that var to
    # re-apply search_path on every pool checkout, so if we don't restore it on
    # exit a stale schema rides the next checkout in this context — the exact
    # connection-leak class this primitive exists to prevent.
    prior_schema = _current_tenant_schema.get()
    session = create_standalone_session(schema)
    session_token = _request_session.set(session)
    # Save the previous user id and restore it on exit so nested
    # usages (rare but possible in test harnesses) don't clobber an
    # outer context.
    user_id_token = _current_user_id.set(None)
    set_current_user_id(user_id)
    try:
        # Arm the transaction-local RLS GUC explicitly for the first
        # transaction.  The ``after_begin`` listener covers subsequent
        # transactions after mid-flight commits, but the very first
        # BEGIN has already fired before we get here (SQLAlchemy begins
        # lazily on the first execute), so we need an explicit call.
        session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": user_id},
        )
        yield session
        # Fail-closed: refuse to commit tenant rows to the shared/default
        # schema.  Matches the guard in DatabaseSessionMiddleware.
        if session.new or session.dirty or session.deleted:
            assert_tenant_schema_set()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        _request_session.reset(session_token)
        _current_user_id.reset(user_id_token)
        # Restore the tenant-schema ContextVar so a stale schema can't ride a
        # later pool checkout in this context (mirrors the _current_user_id
        # restore above). create_standalone_session set it via
        # set_tenant_schema; there is no token, so restore the prior value.
        _current_tenant_schema.set(prior_schema)


async def run_in_tenant[T](
    schema: str,
    user_id: str,
    fn: Callable[..., T],
    /,
    *args: object,
    **kwargs: object,
) -> T:
    """Run a sync callable with a tenant-scoped DB session on a worker thread.

    ``asyncio.to_thread`` copies the current context (ContextVars) into the
    worker, which is what makes ``_request_session`` and ``_current_user_id``
    visible there.  However, a SQLAlchemy session must be opened **inside**
    the worker — sessions are not thread-safe and cannot be shared across
    threads.  This helper wraps *fn* in a thin closure that opens
    ``tenant_db_session`` inside the thread, so the caller never needs to
    think about the session lifecycle.

    The session is passed as the **first positional argument** to *fn*.
    Remaining *args* and *kwargs* are forwarded after it::

        async def my_handler(schema: str, user_id: str, patient_id: str) -> None:
            def _db_work(session: Session) -> None:
                result = session.execute(...)
                ...

            await run_in_tenant(schema, user_id, _db_work)

    If *fn* raises, the session is rolled back and the exception propagates
    to the caller as-is (``tenant_db_session`` handles rollback).

    Args:
        schema: Practice schema name — forwarded verbatim to
            ``tenant_db_session``.
        user_id: Clinician user id — forwarded verbatim.
        fn: Sync callable.  Receives the ``Session`` as its first
            positional argument, followed by ``*args`` and ``**kwargs``.
        *args: Additional positional arguments forwarded to *fn*.
        **kwargs: Keyword arguments forwarded to *fn*.

    Returns:
        Whatever *fn* returns.
    """

    def _in_thread() -> T:
        with tenant_db_session(schema, user_id) as session:
            return fn(session, *args, **kwargs)

    return await asyncio.to_thread(_in_thread)
