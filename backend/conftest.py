# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Shared by both suites: ``tests/`` and ``tests_integration/``.

**No test hands its request principal to the next one.** The engine keeps who
is asking — ``app.current_user_id``, ``app.current_patient_id`` and the tenant
schema — in three request-scoped ContextVars, and the ``after_begin`` listener
re-arms the first two on every new database session. In a request each
ContextVar lives and dies with that request's context. pytest has no such
boundary: every test and fixture runs in the one main-thread context, so a
test that arms a principal directly and never resets it leaves every later
test, in any module, starting out as that user. Integration tests have been
measured failing on exactly that.

So each test, and each module, gets the ContextVars back as it found them.
They are RESTORED rather than blanked: a test or a module fixture that arms a
principal on purpose keeps it for as long as it runs, and it is gone when it
finishes. The two layers are there because they catch different leaks: the
module layer runs before a module's own fixtures and tears down after them,
so a module-scoped fixture that arms and never resets is undone when its
module ends.

The ContextVars are imported inside the fixtures, never at module level:
``tests/conftest.py`` sets the environment the app reads at import, and a
parent conftest is loaded first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from contextvars import ContextVar, Token


def _hold_request_principal() -> list[tuple[ContextVar[Any], Token[Any]]]:
    from app.db import (  # noqa: PLC0415
        _current_patient_id,
        _current_tenant_schema,
        _current_user_id,
    )

    return [
        (var, var.set(var.get()))
        for var in (_current_user_id, _current_patient_id, _current_tenant_schema)
    ]


def _restore_request_principal(tokens: list[tuple[ContextVar[Any], Token[Any]]]) -> None:
    for var, token in reversed(tokens):
        var.reset(token)


@pytest.fixture(scope="module", autouse=True)
def _module_leaves_no_request_principal() -> Iterator[None]:
    tokens = _hold_request_principal()
    yield
    _restore_request_principal(tokens)


@pytest.fixture(autouse=True)
def _test_leaves_no_request_principal() -> Iterator[None]:
    tokens = _hold_request_principal()
    yield
    _restore_request_principal(tokens)
