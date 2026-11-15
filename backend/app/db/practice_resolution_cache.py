# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which practice an email resolves to, cached in Redis for a short time.

Every signed-in request resolves its practice from the token's email before
any route runs (``DatabaseSessionMiddleware``): the email's
``email_tenant_mappings`` row, then the ``practices`` row it points at. Those
are two reads of rows that almost never change, on every request. This module
lets the resolution answer from Redis instead.

What is cached
==============

* ``practice-resolution:v1:email:<sha256(email)>``: the ``(practice_id,
  schema_name)`` an email resolved to, as JSON. Only a positive answer is
  cached: "no practice" is never stored, so a mapping written a moment ago is
  seen on the very next request. The address is hashed so no email sits in
  Redis as a key.
* ``practice-resolution:v1:practice:<practice_id>``: a set of the email keys
  filled for that practice, so a change to the practice can drop all of them.

Every entry carries :data:`TTL_SECONDS`, which bounds how long a change this
module cannot see (raw SQL, or a read that raced a commit) can be served stale.

How it stays right
==================

A listener on every SQLAlchemy ``Session`` watches flushes for practice and
email-mapping rows, and after the COMMIT drops the affected keys. A writer that
goes through the ORM is covered without doing anything, which is what keeps a
deactivated practice from being served from the cache.

* Dropping happens after COMMIT, never at flush: a request reading in between
  would otherwise fill the key again from the row the commit is about to
  replace.
* A transaction that ends with no commit (a rollback, or ``close()``) discards
  what was pending, since none of it committed, so a reused ``Session`` cannot
  carry it into its next commit. A savepoint rollback keeps it: dropping a key
  is always safe.
* An ORM bulk ``update``/``delete`` on either table names no rows, so its
  commit drops every entry.
* A request that read the OLD rows just before a commit and fills just after
  the drop can put the old answer back until the TTL. The TTL bounds it.

Redis is optional and fallible. With ``USE_REDIS=false`` every read is a miss
and resolution reads the database exactly as it did before this module. A
Redis error on read is a miss; a Redis error on write is logged and dropped.
Nothing here ever decides access by itself: the database answer is the
source, and the cache only repeats one it gave recently.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session, SessionTransaction

from ..redis_client import get_redis_client

if TYPE_CHECKING:
    import redis
    from sqlalchemy.orm import ORMExecuteState, UOWTransaction

logger = logging.getLogger(__name__)

#: How long a cached resolution lives. Bounds how long a change the listener
#: cannot see can be served stale; kept short because it is also the bound on
#: a deactivated practice whose deactivation raced a read.
TTL_SECONDS: Final = 60

_PREFIX: Final = "practice-resolution:v1:"
_EMAIL_PREFIX: Final = f"{_PREFIX}email:"
_PRACTICE_PREFIX: Final = f"{_PREFIX}practice:"

_PRACTICES: Final = "practices"
_EMAIL_MAPPINGS: Final = "email_tenant_mappings"

_PENDING_KEY: Final = "practice_resolution_cache_pending"


def _client() -> redis.Redis | None:
    try:
        client: redis.Redis | None = get_redis_client()
    except Exception:
        logger.warning("practice_resolution_cache: Redis client unavailable", exc_info=True)
        return None
    return client


def _email_key(email: str) -> str:
    # Hashed exactly as given: the database lookup is by the exact string too,
    # so two spellings that resolve differently can never share an entry.
    return f"{_EMAIL_PREFIX}{hashlib.sha256(email.encode()).hexdigest()}"


def _practice_key(practice_id: str) -> str:
    return f"{_PRACTICE_PREFIX}{practice_id}"


def get(email: str) -> tuple[str, str] | None:
    """The cached ``(practice_id, schema_name)`` for ``email``, or ``None`` on a miss."""
    client = _client()
    if client is None:
        return None
    try:
        raw = client.get(_email_key(email))
    except Exception:
        logger.warning("practice_resolution_cache: Redis read failed; reading the database")
        return None
    if not raw:
        return None
    try:
        payload = json.loads(str(raw))
        practice_id, schema_name = payload["practice_id"], payload["schema_name"]
    except (ValueError, KeyError, TypeError):
        # A shape this version does not know is a miss, never a guess.
        return None
    if not isinstance(practice_id, str) or not isinstance(schema_name, str):
        return None
    return practice_id, schema_name


def fill(email: str, practice_id: str, schema_name: str) -> None:
    """Cache a resolution the database just gave for ``email``."""
    client = _client()
    if client is None:
        return
    key = _email_key(email)
    index = _practice_key(practice_id)
    value = json.dumps({"practice_id": practice_id, "schema_name": schema_name})
    try:
        pipe = client.pipeline()
        # Index first: a key that exists is always findable from its practice,
        # so a drop by practice cannot miss it.
        pipe.sadd(index, key)
        pipe.expire(index, TTL_SECONDS)
        pipe.set(key, value, ex=TTL_SECONDS)
        pipe.execute()
    except Exception:
        logger.warning("practice_resolution_cache: Redis fill failed")


def _drop(practice_ids: set[str], emails: set[str], *, drop_all: bool) -> None:
    client = _client()
    if client is None:
        return
    try:
        keys = [_email_key(email) for email in emails]
        for practice_id in practice_ids:
            index = _practice_key(practice_id)
            keys += [str(member) for member in client.smembers(index)]
            keys.append(index)
        if drop_all:
            keys += [str(key) for key in client.scan_iter(match=f"{_PREFIX}*")]
        if keys:
            client.delete(*keys)
    except Exception:
        logger.warning("practice_resolution_cache: invalidation failed; entries expire by TTL")


def forget_practice(practice_id: str) -> None:
    """Drop every cached resolution to ``practice_id``.

    The listener already does this for any ORM change to the practice row;
    this is for a writer it cannot see.
    """
    _drop({practice_id}, set(), drop_all=False)


# ---------------------------------------------------------------------------
# The session listener
# ---------------------------------------------------------------------------


class _Pending:
    """What one session's current transaction will drop from the cache on commit."""

    def __init__(self) -> None:
        self.practice_ids: set[str] = set()
        self.emails: set[str] = set()
        self.drop_all = False


def _pending(session: Session) -> _Pending:
    pending = session.info.get(_PENDING_KEY)
    if not isinstance(pending, _Pending):
        pending = _Pending()
        session.info[_PENDING_KEY] = pending
    return pending


def _platform_table_name(table: Any) -> str | None:
    """The table's name when it is one of the two resolution tables, else ``None``."""
    from . import PLATFORM_SCHEMA

    if table is None or getattr(table, "schema", None) != PLATFORM_SCHEMA:
        return None
    name = getattr(table, "name", None)
    return name if name in (_PRACTICES, _EMAIL_MAPPINGS) else None


def _loaded_value(obj: Any, column: str) -> str | None:
    """A column's value from the instance dict, never a lazy load inside a flush."""
    state = inspect(obj)
    value = state.dict.get(column)
    if value is None and state.identity and column in ("id", "email"):
        value = state.identity[0]
    return str(value) if value else None


def _committed_value(obj: Any, column: str) -> str | None:
    """What the column held before this flush changed it, if it was loaded."""
    history = inspect(obj).attrs[column].history
    if history.deleted and history.deleted[0]:
        return str(history.deleted[0])
    return None


def _after_flush(session: Session, _flush_context: UOWTransaction) -> None:
    for obj in (*session.dirty, *session.deleted, *session.new):
        name = _platform_table_name(getattr(type(obj), "__table__", None))
        if name is None:
            continue
        pending = _pending(session)
        if name == _PRACTICES:
            practice_id = _loaded_value(obj, "id")
            if practice_id is None:
                pending.drop_all = True
            else:
                pending.practice_ids.add(practice_id)
            if (old_id := _committed_value(obj, "id")) is not None:
                pending.practice_ids.add(old_id)
            continue
        email = _loaded_value(obj, "email")
        if email is None:
            pending.drop_all = True
        else:
            pending.emails.add(email)
        # A primary-key change leaves the old address cached under its old key.
        if (old_email := _committed_value(obj, "email")) is not None:
            pending.emails.add(old_email)


def _do_orm_execute(state: ORMExecuteState) -> None:
    if not (state.is_update or state.is_delete):
        return
    for mapper in state.all_mappers:
        if _platform_table_name(getattr(mapper, "local_table", None)) is not None:
            _pending(state.session).drop_all = True
            return


def _after_commit(session: Session) -> None:
    pending = session.info.pop(_PENDING_KEY, None)
    if not isinstance(pending, _Pending):
        return
    try:
        _drop(pending.practice_ids, pending.emails, drop_all=pending.drop_all)
    except Exception:
        # A cache problem must never surface as a failed commit.
        logger.warning("practice_resolution_cache: post-commit drop failed", exc_info=True)


def _after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    """Discard what was pending when the outermost transaction ends without a commit.

    ``after_commit`` has already taken it for a commit, so this only matters for
    a rollback or a ``close()``. ``session.info`` outlives both, so without this
    a reused session's next commit would drop keys for writes that never
    committed. A savepoint ending is not the outermost transaction, so what it
    flushed stays pending: dropping a key is always safe.
    """
    if transaction.parent is None:
        session.info.pop(_PENDING_KEY, None)


_REGISTERED_FLAG: Final = "_pablo_practice_resolution_cache_listening"


def register() -> None:
    """Attach the listener to every ``Session``. Idempotent."""
    if getattr(Session, _REGISTERED_FLAG, False):
        return
    event.listen(Session, "after_flush", _after_flush)
    event.listen(Session, "do_orm_execute", _do_orm_execute)
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_transaction_end", _after_transaction_end)
    setattr(Session, _REGISTERED_FLAG, True)


register()
