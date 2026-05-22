# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pytest helper that fails when a SQLAlchemy pool checkout happens
inside a marked region — designed to pin the contract for
``StreamingResponse`` route handlers.

Background: ``BaseHTTPMiddleware`` returns from ``call_next`` once the
response headers ship; the body iterator keeps running afterward in a
separate task. If a route handler does DB work from inside that body
iterator, it can grab a pool connection whose server-side
``search_path`` reflects whichever request used it last — fine if the
session middleware's reapply-on-checkout listener (``app.db.
_reapply_search_path_on_checkout``) is in place, **broken** the
moment that protection regresses.

This guard catches the regression class at test time. Wrap the
read-the-body call in :func:`assert_no_db_checkouts` and any pool
checkout that fires during it will raise an ``AssertionError`` with a
short stack trace pointing at the offending call site.

Example::

    from ._streaming_body_guard import assert_no_db_checkouts

    def test_chat_streams_without_post_response_db_work(client):
        resp = client.post(...)
        assert resp.status_code == 200
        with assert_no_db_checkouts("chat SSE body"):
            body = resp.content  # iterates the response body
        # body is now fully consumed — gate runs in the finally clause

Not a replacement for the runtime guard (``_reapply_search_path_on_
checkout``) — that one defends production. This one defends the
contract in CI so a future refactor doesn't quietly un-fix the bug.

Companion of THERAPY-j62m / THERAPY-g75j.
"""

from __future__ import annotations

import threading
import traceback
from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.engine import Engine

if TYPE_CHECKING:
    from collections.abc import Iterator

# Thread-local: the listener fires on whichever thread the checkout
# happens. We use thread-local state so a concurrent test on another
# thread doesn't false-trigger.
_state = threading.local()


def _get_state() -> threading.local:
    if not hasattr(_state, "active"):
        _state.active = False
        _state.label = ""
        _state.checkouts = []
    return _state


@event.listens_for(Engine, "checkout")
def _record_checkout_during_guard(  # type: ignore[no-untyped-def]
    _dbapi_conn, _conn_record, _conn_proxy
) -> None:
    """Pool-checkout listener. No-op outside an :func:`assert_no_db_checkouts`
    block; otherwise records the call-site stack for the assertion message.
    """
    s = _get_state()
    if not s.active:
        return
    # Capture the caller's stack at checkout time — useful when the
    # assertion fires because the failure path is otherwise opaque
    # (you'd just see "DB checkout happened" with no hint of where).
    s.checkouts.append(traceback.extract_stack())


@contextmanager
def assert_no_db_checkouts(label: str = "streaming-body") -> Iterator[None]:
    """Context manager — fail the surrounding test if any SQLAlchemy
    pool checkout happens inside the block.

    ``label`` shows up in the assertion message; pick something
    descriptive (e.g. ``"chat SSE body"``, ``"tenant export body"``).

    Not re-entrant: nesting raises ``RuntimeError`` to surface the
    test-author error.
    """
    s = _get_state()
    if s.active:
        msg = "assert_no_db_checkouts is not re-entrant"
        raise RuntimeError(msg)
    s.active = True
    s.label = label
    s.checkouts = []
    try:
        yield
    finally:
        captured = list(s.checkouts)
        s.active = False
        s.label = ""
        s.checkouts = []
    if captured:
        raise AssertionError(_format_failure(label, captured))


def _format_failure(label: str, checkouts: list[list[traceback.FrameSummary]]) -> str:
    """Build a multi-line assertion message: count + a few call sites.

    Truncates after 3 checkouts and 5 frames each so the failure
    output is digestible — full traces are available via the debugger
    if needed.
    """
    lines = [
        f"Unexpected DB pool checkout inside `{label}` block "
        f"(count={len(checkouts)}). The route handler may be doing DB "
        "work during StreamingResponse body iteration — see "
        "_streaming_body_guard.py for the failure mode (j62m)."
    ]
    for i, stack in enumerate(checkouts[:3]):
        lines.append(f"  Checkout #{i + 1} call site (innermost 5 frames):")
        for frame in stack[-5:]:
            lines.append(f"    {frame.filename}:{frame.lineno} in {frame.name}")
    if len(checkouts) > 3:
        lines.append(f"  ...and {len(checkouts) - 3} additional checkouts elided")
    return "\n".join(lines)
