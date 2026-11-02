# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Refuse a request's write into the provisioning template schema when it is sent.

The ``practice`` schema is the template every new practice is built from. It
has no row security, and nothing written there belongs to anyone, so a request
must never write to it. The request middleware checks the search path at
commit, but only while changes are still pending: a repository that flushes
before the commit has already sent its rows, and the commit then has nothing
left to check. So the write is checked where it is sent — at the flush, and
for an ORM ``insert``/``update``/``delete`` statement, at execution.

Only tables that resolve through the search path are checked. A table with an
explicit schema (``platform.*``) lands where it says whatever the path is.

Armed per session by ``guard_template_writes``; the request middleware arms
the session it creates. Provisioning and migration code, which builds the
template on purpose, never sees it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import event, inspect, text
from sqlalchemy.orm import ORMExecuteState, Session

from . import DEFAULT_PRACTICE_SCHEMA

if TYPE_CHECKING:
    from sqlalchemy.orm import UOWTransaction

logger = logging.getLogger(__name__)

_GUARD_KEY = "pablo.guard_template_writes"


class TemplateSchemaWriteError(RuntimeError):
    """A request tried to write a practice table while on the template schema."""


def guard_template_writes(session: Session) -> None:
    """Refuse any write this session sends to a practice table on the template schema."""
    session.info[_GUARD_KEY] = True


def _search_path_head(session: Session) -> str:
    search_path = session.connection().execute(text("SHOW search_path")).scalar() or ""
    return search_path.split(",")[0].strip().strip('"')


def _refuse_if_on_template(session: Session, what: str) -> None:
    if _search_path_head(session) != DEFAULT_PRACTICE_SCHEMA:
        return
    logger.error(
        "TENANT ISOLATION VIOLATION blocked: %s into the provisioning template schema '%s'",
        what,
        DEFAULT_PRACTICE_SCHEMA,
    )
    msg = (
        "Tenant isolation violation: data would be written to the shared template "
        "schema instead of a practice's schema. This request has been blocked."
    )
    raise TemplateSchemaWriteError(msg)


def _follows_search_path(mapped: Any) -> bool:
    return any(table.schema is None for table in inspect(mapped).mapper.tables)


@event.listens_for(Session, "before_flush")
def _refuse_flush_into_template(session: Session, _flush_context: UOWTransaction, _: Any) -> None:
    if not session.info.get(_GUARD_KEY):
        return
    pending = (*session.new, *session.dirty, *session.deleted)
    if any(_follows_search_path(obj) for obj in pending):
        _refuse_if_on_template(session, "flush")


@event.listens_for(Session, "do_orm_execute")
def _refuse_statement_into_template(state: ORMExecuteState) -> None:
    if not (state.is_insert or state.is_update or state.is_delete):
        return
    if not state.session.info.get(_GUARD_KEY):
        return
    table = getattr(state.statement, "table", None)
    if table is not None and table.schema is None:
        _refuse_if_on_template(state.session, "statement")
