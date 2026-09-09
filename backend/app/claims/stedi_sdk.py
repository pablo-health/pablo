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

if TYPE_CHECKING:
    from .credentials import ClearinghouseCredentials


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

    The client holds a connection pool and is an async context manager; callers
    that make more than one call should hold it open rather than building one
    per request.
    """
    return Stedi(sdk_config(credentials))
