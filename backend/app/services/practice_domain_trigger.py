# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Asking for the domain reconciler to run now: the extension point.

The web backend makes no cloud calls for a practice's hosts; the reconciler
job does. So that a host is served soon after it is added, rather than at the
next scheduled run, the backend asks for a run whenever it leaves the job
something to do: a host added, a host removed, or a DNS check that left a host
waiting. How a run is started is configurable per deployment — the engine
cannot know whether the job is a container, a cron entry or something else —
so the engine ships no trigger: with none registered, asking does nothing and
the job's own schedule picks the work up.

Asking never fails the request that asked. A trigger that raises is logged and
forgotten; the work is still recorded and the next run does it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_trigger: Callable[[], None] | None = None


def register_reconcile_trigger(fn: Callable[[], None]) -> None:
    """Supply what starts a reconciler run. Called once at startup."""
    global _trigger  # noqa: PLW0603
    _trigger = fn


def request_reconcile() -> None:
    """Ask for a reconciler run, if the deployment registered a way to start one.

    Called after the request's writes have committed, so the run sees them.
    """
    if _trigger is None:
        return
    try:
        _trigger()
    except Exception:
        # The host is recorded whatever happens here; the next run serves it.
        logger.warning("practice_domain_reconcile_trigger_failed", exc_info=True)


def reset_reconcile_trigger() -> None:
    """Drop the registration. For tests, so one case's trigger does not leak
    into the next through the module-level slot."""
    global _trigger  # noqa: PLW0603
    _trigger = None
