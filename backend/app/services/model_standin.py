# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Send the model calls of named practices to a stand-in, and no one else's.

A deployment that runs automated end-to-end suites against a shared
environment would otherwise pay for a real model call on every run and get a
different answer each time. ``MODEL_STANDIN_PRACTICES`` names the practices
whose calls go to ``MODEL_STANDIN_BASE_URL`` instead (``scripts/fake_llm.py``
serves it under ``/standin``); every other practice on the same deployment
keeps its real models, its fallbacks and its effort settings unchanged.

The practice is the one the current database session is scoped to: the
schema ``set_tenant_schema`` records for the request, job or unit of work in
progress. It is never read from anything the caller sends. A call made with no
practice in scope (a platform job, an unauthenticated route) is never routed.

The rule is asked on every call, at the point the call is made, so a gateway
built before its job's practice was in scope still routes correctly. The gateway
factories ask it (``resolve_structured_llm_gateway``, ``failover_chat_gateway``,
``get_dictation_transcriber``), so their callers need no change. Code layered on
the engine that builds its own model client asks :func:`model_standin_base_url`
the same way.
"""

from __future__ import annotations

import logging
import threading

from ..db import DEFAULT_PRACTICE_SCHEMA, PLATFORM_SCHEMA, _current_tenant_schema
from ..settings import get_settings

logger = logging.getLogger(__name__)

#: Practice schema -> practice id, or ``None`` for a schema no practice owns.
#: A practice's schema is fixed when it is provisioned and never reassigned, so
#: an answer is good for the life of the process. Only consulted while a
#: practice is listed, so a deployment with none never looks anything up.
_practice_by_schema: dict[str, str | None] = {}
_lock = threading.Lock()


def _practice_id_for(schema: str) -> str | None:
    """The id of the practice whose schema this is, looked up once per process."""
    with _lock:
        if schema in _practice_by_schema:
            return _practice_by_schema[schema]
    from sqlalchemy import select

    from ..db import create_standalone_session
    from ..db.platform_models import PracticeRow

    with create_standalone_session() as db:
        practice_id = db.execute(
            select(PracticeRow.id).where(PracticeRow.schema_name == schema)
        ).scalar_one_or_none()
    with _lock:
        _practice_by_schema[schema] = practice_id
    return practice_id


def model_standin_base_url() -> str | None:
    """The stand-in's base URL when the practice in scope is listed, else ``None``.

    ``None`` means: call the real model, exactly as if this module did not
    exist. That is also the answer when the practice cannot be looked up, since
    a listed practice reaching a real model costs a call, while an unlisted one
    reaching the stand-in would hand a clinician text no model wrote.
    """
    settings = get_settings()
    listed = settings.model_standin_practices
    if not listed:
        return None
    schema = _current_tenant_schema.get()
    if not schema or schema in (DEFAULT_PRACTICE_SCHEMA, PLATFORM_SCHEMA):
        return None
    try:
        practice_id = _practice_id_for(schema)
    except Exception as exc:
        # Exception type only: the message can carry connection details.
        logger.warning("model stand-in: practice lookup failed: %s", type(exc).__name__)
        return None
    if practice_id is None or practice_id not in listed:
        return None
    return settings.model_standin_base_url


def _forget_practices() -> None:
    """Drop the schema lookups. For tests."""
    with _lock:
        _practice_by_schema.clear()


__all__ = ["model_standin_base_url"]
