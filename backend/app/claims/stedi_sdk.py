# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Constructing the vendor's SDK client from a practice's credentials.

The adapter in ``app.claims.stedi`` predates the vendor's generated SDK and
calls the older compatibility endpoints over ``httpx``; operations move onto
the SDK one at a time, and this module is where the client they need comes
from. The payer directory and enrollment stay on ``httpx`` because the SDK
does not cover them.

**Both async functions here must run on the SDK loop.** Constructing the
SDK's ``Config`` eagerly builds an ``aiohttp.ClientSession``, which needs a
running loop, so a client cannot be built at import time and shared with
synchronous callers.

``base_url`` replaces the origin only, like ``ApiBases.resolve``, so one
stand-in (the end-to-end harness) can answer for every vendor host.
``test_clearinghouse_base_url.py`` pins that equivalence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stedi import Stedi
from stedi.config import Config

from .sdk_runtime import run_on_sdk_loop, sdk_loop_running, shutdown_sdk_loop

if TYPE_CHECKING:
    from .credentials import ClearinghouseCredentials

#: Shutdown waits on in-flight sessions closing, not on a network round trip.
_SHUTDOWN_TIMEOUT_SECONDS = 10.0


def sdk_config(credentials: ClearinghouseCredentials) -> Config:
    """The SDK configuration for these credentials.

    ``endpoint_uri`` stays unset for real deployments.
    """
    config = Config(api_key=credentials.api_key)
    if credentials.base_url:
        config.endpoint_uri = credentials.base_url.rstrip("/")
    return config


def sdk_client(credentials: ClearinghouseCredentials) -> Stedi:
    """An unshared client, for tests.

    Prefer :func:`client_for`: a client holds a connection pool, and one per
    call leaks the session behind it.
    """
    return Stedi(sdk_config(credentials))


#: Process-lifetime clients keyed by credentials, because the default provider
#: re-reads settings per call and a bare singleton would pin the first key it
#: saw. Only touched from the single-threaded SDK loop, so no lock.
_clients: dict[tuple[str, str | None], Stedi] = {}


async def client_for(credentials: ClearinghouseCredentials) -> Stedi:
    """The shared client for these credentials, built on first use.

    Must be awaited on the SDK loop (:mod:`app.claims.sdk_runtime`); a client
    built on another loop cannot be reused from this one.
    """
    key = (credentials.api_key, credentials.base_url)
    client = _clients.get(key)
    if client is None:
        client = Stedi(sdk_config(credentials))
        _clients[key] = client
    return client


async def close_clients() -> None:
    """Close every cached client. Awaited on the SDK loop at shutdown."""
    while _clients:
        _, client = _clients.popitem()
        await client.__aexit__(None, None, None)


def shutdown_sdk() -> None:
    """Release everything this module owns. Call once on application shutdown.

    Clients close before the loop stops because sessions must be closed by the
    loop that opened them. A deployment that never started the loop must not
    start one just to stop it.
    """
    if not sdk_loop_running():
        return
    run_on_sdk_loop(close_clients(), timeout=_SHUTDOWN_TIMEOUT_SECONDS)
    shutdown_sdk_loop()
