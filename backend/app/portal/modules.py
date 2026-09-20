# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which patient-facing modules this deployment actually serves.

A practice's portal is not one screen. Depending on what the practice does,
it is some of: a form to fill in before a first visit, a way to write to the
practice, documents to read, appointments to see, a balance to settle, a
between-visit conversation. Turning those on and off is a deployment's
decision, and the shell needs to know the answer so it can show a patient
the things that exist and nothing else.

**The answer is an intersection, and that is the whole design.** One half is
the configured list (``PORTAL_MODULES``). The other half is what is MOUNTED
— read off the running application's own route table, not off a second
belief about it. A module that is configured but whose routes are not there
is reported off, because it is off; the patient would get a 404 either way,
and a navigation item that leads to one is worse than no navigation item.

That ordering is what keeps the honest sentence honest. Hiding a module in
the shell is not an authorization control and was never meant to be: the
control is that ``app.main`` does not mount an unlisted module's router, so
its paths answer 404 to a patient holding a perfectly good session. This
module reports that fact rather than restating it, so the two cannot drift.

**A deployment layered over this one may narrow the list and may not widen
it.** Whatever wraps :func:`portal_capabilities` can drop modules — a
per-practice entitlement, a plan that does not include messaging — by
passing a smaller ``configured``. It cannot add one, because the mounted set
is read from the routes that exist and nothing outside this process can put
a route there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from ..route_introspection import iter_api_routes

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from fastapi import FastAPI

#: Every module name the portal knows, in the order a patient meets them.
#:
#: The order is the shell's navigation order, so it is a product decision
#: recorded here rather than an alphabetical accident: paperwork first
#: because it is what a practice asks for before a first visit, then the
#: channels, then the things a patient checks rather than does.
PORTAL_MODULE_NAMES: Final[tuple[str, ...]] = (
    "intake",
    "messaging",
    "documents",
    "appointments",
    "billing",
    "chat",
)

#: One mounted path per module — the thing whose presence in the route table
#: means "this module is served here".
#:
#: A path rather than a router object, because the question being asked is
#: about the ASSEMBLED application: a router that exists as a Python object
#: and was never included proves nothing. These are matched against the live
#: route table, so a typo here reads as "module off" — the safe direction,
#: and one the capability tests catch immediately.
#:
#: ``documents`` and ``billing`` name paths that nothing mounts yet. That is
#: deliberate and is the mechanism working: both are real portal modules
#: with clinician-side routes already built and no patient-facing surface,
#: so they report off until one lands, whatever the configuration says.
MODULE_MARKER_PATHS: Final[Mapping[str, str]] = {
    "intake": "/api/patient/intake/form",
    "messaging": "/api/patient/messages/threads",
    "documents": "/api/patient/documents",
    "appointments": "/api/patient/appointments",
    "billing": "/api/patient/billing/summary",
    "chat": "/api/patient/chat/conversations",
}


def known_modules(configured: Iterable[str]) -> tuple[str, ...]:
    """The configured names that are modules, in :data:`PORTAL_MODULE_NAMES` order.

    Unknown names are dropped rather than refused: a deployment rolled back
    to an older image should keep serving the modules that image has, not
    fail to boot over a name from the newer one.
    """
    wanted = set(configured)
    return tuple(name for name in PORTAL_MODULE_NAMES if name in wanted)


def mounted_modules(paths: Iterable[str]) -> frozenset[str]:
    """Which modules the given route paths show to be served.

    ``paths`` is a flat sequence of FULL path templates. Nothing here
    interprets them beyond an exact match against
    :data:`MODULE_MARKER_PATHS`. Callers holding a ``FastAPI`` should use
    :func:`mounted_modules_on` rather than build the sequence themselves.
    """
    present = set(paths)
    return frozenset(name for name, marker in MODULE_MARKER_PATHS.items() if marker in present)


def mounted_modules_on(app: FastAPI) -> frozenset[str]:
    """Which modules this application actually serves.

    Goes through ``app.route_introspection`` rather than reading
    ``app.routes`` directly, and that is not a stylistic preference:
    fastapi 0.137 turned ``app.routes`` from a flat list into a tree, so a
    naive walk reports only the handful of routes declared on the
    application object and MISSES every router that was included — which is
    all of them. Read that way, every module would report off forever, and
    the failure looks like a configuration problem rather than a traversal
    one.
    """
    return mounted_modules(path for path, _route in iter_api_routes(app))


def portal_capabilities(*, configured: Sequence[str], mounted: frozenset[str]) -> dict[str, bool]:
    """The capability document: every known module, and whether it is on.

    Every module appears, including the off ones. A client that only saw the
    enabled names would have to know the full list to render anything at all
    about the rest, and would silently ignore a module added later; one that
    is handed the whole map can tell "off" from "I have never heard of it".
    """
    enabled = set(known_modules(configured)) & mounted
    return {name: name in enabled for name in PORTAL_MODULE_NAMES}


__all__ = [
    "MODULE_MARKER_PATHS",
    "PORTAL_MODULE_NAMES",
    "known_modules",
    "mounted_modules",
    "mounted_modules_on",
    "portal_capabilities",
]
