# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Subscription access levels and route read/write intent.

A subscription is not simply on or off. Deployments that track
subscriptions need a middle state: a practice whose subscription has
ended still owns its clinical records, and a clinician who can no
longer create new notes must still be able to open the chart and
export it. Hard-blocking every request the moment a subscription
lapses answers a question about payment by taking away access to
records, which for a clinical system is the wrong trade.

So a subscription carries an **access level**, and every route
declares — implicitly by HTTP method, explicitly when the method
lies — whether it *reads* or *writes*:

1. **FULL** — every route is allowed. This is what an active or
   trialing subscription resolves to, and it is what a deployment
   that tracks no subscriptions at all behaves like.

2. **READ_ONLY** — read-intent routes are allowed; write-intent
   routes are refused with ``SUBSCRIPTION_READONLY``. The practice
   keeps indefinite view-and-export access to everything it already
   recorded, and stops accumulating new records.

3. **NONE** — no route behind the subscription gate is allowed;
   the request is refused with ``SUBSCRIPTION_INACTIVE``.

Read/write intent defaults to the HTTP method — ``GET``, ``HEAD``
and ``OPTIONS`` read, everything else writes. A handful of routes
contradict their method, and those are listed explicitly in
``_INTENT_OVERRIDES``: a ``POST`` that computes a preview and
persists nothing is a read, and a ``GET`` that completes an OAuth
handshake and stores the resulting tokens is a write. Getting this
backwards is a real failure — a mis-classified ``GET`` is a silent
write that survives the wind-down — so
``tests/test_subscription_access_guardrails.py`` re-derives the
classification from the live route table and fails on anything it
cannot account for.

None of this is load-bearing for a deployment that does not track
subscriptions. When the subscription record carries no access level,
:func:`resolve_access_level` falls back to
:func:`derive_access_level`, which reproduces the original
active-or-nothing behavior exactly.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)


class AccessLevel(StrEnum):
    """How much of the API a subscription state opens up.

    The values are deliberately a small closed set rather than a
    per-route capability list: the distinction that matters
    clinically is "can this practice still record new work" versus
    "can it still read what it recorded", and that is one bit plus
    an off state. Per-route rules live at the route, as read/write
    intent, where the handler's actual behavior is visible.

    An unrecognized value resolves to :attr:`NONE`, not to
    :attr:`FULL` — an access level nobody in this process
    understands must not be read as permission.
    """

    FULL = "full"
    READ_ONLY = "read_only"
    NONE = "none"


class AccessIntent(StrEnum):
    """Whether a route reads existing records or records new ones.

    This is about the handler's effect, not its HTTP method. The
    method is the default signal (see :data:`READ_METHODS`); this
    enum is what an override states when the method is misleading.
    """

    READ = "read"
    WRITE = "write"


#: HTTP methods that read by default. Everything else writes by
#: default. ``OPTIONS`` is included so CORS preflight is never the
#: thing that fails a wind-down.
READ_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

#: Subscription statuses that open up the whole API when the
#: subscription record carries no explicit access level.
_FULL_ACCESS_STATUSES: frozenset[str] = frozenset({"active", "trial"})


def derive_access_level(effective_status: str | None) -> AccessLevel:
    """Infer an access level from a bare subscription status.

    The compatibility path: a subscription record that predates
    access levels, or a deployment that never sets one, is either
    active/trialing (:attr:`AccessLevel.FULL`) or not
    (:attr:`AccessLevel.NONE`). That is the original behavior of the
    subscription gate, preserved exactly — no record gains read-only
    access by accident, it has to be granted.
    """
    if effective_status in _FULL_ACCESS_STATUSES:
        return AccessLevel.FULL
    return AccessLevel.NONE


def resolve_access_level(sub: Mapping[str, Any]) -> AccessLevel:
    """Read the access level a subscription record grants.

    An explicit ``access_level`` wins. Absent one, fall back to
    :func:`derive_access_level` over ``effective_status`` (or
    ``status``), which is the pre-access-level behavior.

    A present-but-unrecognized ``access_level`` resolves to
    :attr:`AccessLevel.NONE` and is logged. Falling back to the
    status-derived level there would be worse: the record explicitly
    asked for a level this build does not implement, and guessing
    around that is how a restricted subscription silently becomes a
    full one after a rollback.
    """
    if "access_level" not in sub:
        return derive_access_level(sub.get("effective_status", sub.get("status")))

    raw = sub.get("access_level")
    if isinstance(raw, str):
        try:
            return AccessLevel(raw)
        except ValueError:
            pass

    logger.warning(
        "Unrecognized subscription access_level %r — treating as no access",
        raw,
    )
    return AccessLevel.NONE


#: Routes whose HTTP method misstates what the handler does. Keyed by
#: ``(method, path_template)`` where the path template is the route's
#: declared path (``/api/patients/{patient_id}``), not a resolved URL.
#:
#: Every entry needs a reason, and the reason has to be about
#: persistence — does the handler leave anything behind?
_INTENT_OVERRIDES: dict[tuple[str, str], AccessIntent] = {
    # POSTs that persist nothing. Both take a request body only
    # because the query is too large for a query string.
    ("POST", "/api/chat/conversations/preview"): AccessIntent.READ,
    ("POST", "/api/availability/check"): AccessIntent.READ,
    # GETs that persist. The calendar OAuth handshake is a browser
    # redirect flow, so both legs have to be GETs, and the callback
    # stores the tokens it was issued.
    ("GET", "/api/google-calendar/authorize"): AccessIntent.WRITE,
    ("GET", "/api/google-calendar/callback"): AccessIntent.WRITE,
}


def register_intent_override(
    method: str,
    path_template: str,
    intent: AccessIntent,
) -> None:
    """Classify a route this module does not know about.

    A deployment may mount routers of its own on top of this app.
    Those routes get the same method-based default as everything
    else; this is how one that contradicts its method declares
    itself, without editing a table it does not own.

    Call it at import time, before the first request is served.
    """
    _INTENT_OVERRIDES[(method.upper(), path_template)] = intent


def access_intent(method: str, path_template: str) -> AccessIntent:
    """Classify a route as reading or writing.

    An explicit override wins; otherwise the HTTP method decides.
    """
    method = method.upper()
    override = _INTENT_OVERRIDES.get((method, path_template))
    if override is not None:
        return override
    return AccessIntent.READ if method in READ_METHODS else AccessIntent.WRITE


def subscription_exempt() -> None:
    """Marker dependency: this route works in any subscription state.

    A no-op dependency, and a sibling of
    :func:`app.auth.route_security.truly_public` in spirit: it makes
    a decision explicit at the route so it shows up in the diff.
    ``Depends(subscription_exempt)`` says:

    - This route is deliberately outside the subscription gate.
    - It stays reachable whatever the subscription state is —
      including no access at all.

    It suits the routes a user needs precisely *because* their
    subscription is in trouble (their own profile, account status,
    agreements, audit trail) and the catalog routes that carry no
    practice data. It does not suit anything that reads or records
    patient data; that belongs behind the gate, where the access
    level decides.

    ``tests/test_subscription_access_guardrails.py`` requires every
    route to be gated, exempt by auth posture, or marked with this —
    so a new route cannot slip past the question by omission.
    """
    return None
