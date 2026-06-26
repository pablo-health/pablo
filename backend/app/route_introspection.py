# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Flatten the FastAPI route tree into ``(full_path, route)`` pairs.

fastapi 0.137 changed ``app.routes`` from a flat list of ``APIRoute`` objects
into a nested tree: ``include_router()`` now stores each included router as an
intermediate node instead of copying its path operations up to the top level.
Code that iterates ``app.routes`` expecting a flat list silently misses every
route mounted via ``include_router`` — i.e. almost all of them.

``iter_route_contexts`` (fastapi >= 0.137.2) walks that tree and reports each
route's *full* effective path. This module wraps it behind a small helper so
callers introspect routes in one place instead of re-deriving the traversal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.routing import APIRoute, iter_route_contexts

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from starlette.routing import BaseRoute


def iter_app_routes(app: FastAPI) -> Iterator[tuple[str, BaseRoute]]:
    """Yield ``(full_path, route)`` for every route mounted on ``app``.

    ``full_path`` is the complete path template including every router prefix
    (e.g. ``/api/auth/native/code``), not the router-relative declaration.
    Includes non-API routes (``/openapi.json``, ``/docs``, …); filter on
    ``isinstance(route, APIRoute)`` (or use :func:`iter_api_routes`) if you
    only want path operations.
    """
    for ctx in iter_route_contexts(app.routes):
        path = ctx.path
        if isinstance(path, str):
            yield path, ctx.original_route


def iter_api_routes(app: FastAPI) -> Iterator[tuple[str, APIRoute]]:
    """Yield ``(full_path, route)`` for every ``APIRoute`` (path operation)."""
    for path, route in iter_app_routes(app):
        if isinstance(route, APIRoute):
            yield path, route
