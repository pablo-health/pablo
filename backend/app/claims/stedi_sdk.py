# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Constructing the vendor's own SDK client from a practice's credentials.

The vendor publishes a generated SDK covering its current APIs — eligibility,
professional claims, the claim lifecycle, and event destinations. The adapter
in ``app.claims.stedi`` predates it and calls the vendor's older
compatibility endpoints over ``httpx``; moving operation by operation onto the
SDK is what this module exists for. It deliberately starts small: everything
here is about *where* the SDK sends its calls and *who* it authenticates as,
so that the first operation to move has somewhere to be constructed from.

Two hosts' worth of operations stay on ``httpx`` for now — the payer directory
and enrollment — because the SDK does not cover them.

**Both functions here must be called from a running event loop.** Constructing
the SDK's ``Config`` eagerly builds its default transport, and that builds an
``aiohttp.ClientSession``, which has no loop to attach to otherwise. So a
client cannot be built once at import or dependency-injection time and reused
by synchronous callers — it is constructed inside the async call that needs
it, which is also why callers making several calls should hold one open rather
than paying for a pool per request.

``base_url``: a deployment that has to be answered by something other than the
vendor (the end-to-end harness's stand-in clearinghouse) says so once, on the
credentials, and both clients honour it. The SDK spells this ``endpoint_uri``
and, like ``ApiBases.resolve``, it replaces the *origin only* — each operation
keeps its own version path, which is why one stand-in can answer for every
host. That equivalence is what
``backend/tests/test_clearinghouse_base_url.py`` pins.
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
    """The SDK configuration a practice's credentials describe.

    ``endpoint_uri`` is left unset for every real deployment, which is what
    routes each operation to the vendor's own host for that API.
    """
    config = Config(api_key=credentials.api_key)
    if credentials.base_url:
        config.endpoint_uri = credentials.base_url.rstrip("/")
    return config


def sdk_client(credentials: ClearinghouseCredentials) -> Stedi:
    """An SDK client for this practice's account.

    Prefer :func:`client_for` — a client holds a connection pool, and building
    one per call throws away every keep-alive connection and leaks the session
    behind it. This exists for tests that want an unshared client.
    """
    return Stedi(sdk_config(credentials))


#: Clients live as long as the process, keyed by the credentials they
#: authenticate with. Keyed rather than a single module-level client because
#: ``SettingsClearinghouseCredentialProvider`` deliberately re-reads settings
#: on every call, so that a redeployed API key takes effect without a code
#: change; a bare singleton would pin the first key it ever saw and go on
#: authenticating as it. Only ever touched from the SDK loop, which is
#: single-threaded, so it needs no lock of its own.
_clients: dict[tuple[str, str | None], Stedi] = {}


async def client_for(credentials: ClearinghouseCredentials) -> Stedi:
    """The shared client for these credentials, built on first use.

    Must be awaited on the SDK loop (see :mod:`app.claims.sdk_runtime`): the
    client cannot be constructed off a running loop at all, and one built on a
    different loop could not be reused from this one.
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

    Closing the clients before stopping the loop is the order that matters:
    the sessions have to be closed *by* the loop they were opened on, and a
    stopped loop cannot run the coroutine that closes them.

    A deployment that never calls the clearinghouse never starts the loop, and
    this must not start one just to stop it again.
    """
    if not sdk_loop_running():
        return
    run_on_sdk_loop(close_clients(), timeout=_SHUTDOWN_TIMEOUT_SECONDS)
    shutdown_sdk_loop()
