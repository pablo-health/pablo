# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Calling the vendor's async SDK from this application's synchronous code.

The vendor's SDK is async-only. Everything that calls the clearinghouse here
is synchronous, and has to stay that way: the database is synchronous
SQLAlchemy, and the routes that file claims and check coverage are sync ``def``
so FastAPI runs them in its threadpool rather than blocking the event loop on
every query. Making those routes ``async def`` without an async database
driver would put blocking I/O on the shared loop, which no test in this suite
would catch.

So the seam stays synchronous and the asynchrony is contained here: one event
loop, owned by this module, running on a daemon thread for the life of the
process. A synchronous caller hands a coroutine to :func:`run_on_sdk_loop`,
which marshals it onto that loop and blocks on the result.

Owning the loop rather than making one per call is what makes this safe. An
SDK client — and the ``aiohttp`` session inside it — is bound to the loop it
was created on, so a client cached across ``asyncio.run()`` calls would raise
"attached to a different loop" the moment it was reused. That would not show
up in a test suite that runs one loop; it would show up on the first tick of
``claims_pipeline_loop``, which does its work in a worker thread. With a single
long-lived loop the question does not arise, and callers may be on any thread.

The per-call timeout is deliberate and belongs to the caller's side of the
boundary: a coroutine that never completes would otherwise pin the calling
thread forever, and in a threadpool that is how a service stops answering.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Any

from smithy_core.exceptions import CallError
from stedi import models as sdk_models

from .clearinghouse import (
    ClearinghouseAccessDeniedError,
    ClearinghouseInFlightError,
    ClearinghouseNotFoundError,
    ClearinghouseRateLimitedError,
    ClearinghouseUnavailableError,
    ClearinghouseValidationError,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

logger = logging.getLogger(__name__)

#: How long a synchronous caller will wait for one SDK call. Generous next to
#: the adapter's own 20s request timeout, because this bounds the whole
#: operation — the SDK's internal retries included — rather than one request.
DEFAULT_CALL_TIMEOUT_SECONDS = 90.0


class _SdkLoop:
    """One event loop on a daemon thread, started on first use."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None:
                return self._loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, name="clearinghouse-sdk-loop", daemon=True
            )
            thread.start()
            self._loop, self._thread = loop, thread
            logger.info("clearinghouse_sdk_loop_started")
            return loop

    def started(self) -> bool:
        with self._lock:
            return self._loop is not None

    def close(self) -> None:
        """Stop the loop and join its thread. Safe to call when never started."""
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop, self._thread = None, None
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)
        loop.close()
        logger.info("clearinghouse_sdk_loop_stopped")


_sdk_loop = _SdkLoop()


def run_on_sdk_loop[T](
    coro: Coroutine[Any, Any, T], *, timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS
) -> T:
    """Run ``coro`` on this module's loop and block until it finishes.

    Callable from any thread, including FastAPI's request threadpool and the
    worker thread the claims pipeline runs in.
    """
    future = asyncio.run_coroutine_threadsafe(coro, _sdk_loop.loop())
    try:
        return future.result(timeout=timeout)
    except TimeoutError as exc:
        future.cancel()
        msg = f"the clearinghouse did not answer within {timeout:.0f}s"
        raise ClearinghouseUnavailableError(msg) from exc


def sdk_loop_running() -> bool:
    """Whether the loop has been started — i.e. whether anything used the SDK."""
    return _sdk_loop.started()


def shutdown_sdk_loop() -> None:
    """Stop the loop. Call once on application shutdown."""
    _sdk_loop.close()


def translate_sdk_error(exc: Exception) -> Exception:
    """The vendor SDK's exception, as one of this package's own.

    Callers of ``ClearinghouseClient`` already handle the taxonomy in
    ``app.claims.clearinghouse``; which client implementation raised is not
    their business. Anything unrecognised becomes
    :class:`ClearinghouseUnavailableError` rather than escaping as a vendor
    type, so a caller's ``except ClearinghouseError`` cannot be silently
    bypassed by an SDK exception nobody anticipated.
    """
    message = str(exc)
    match exc:
        case sdk_models.InvalidRequestException() | sdk_models.ContentTooLargeException():
            return ClearinghouseValidationError(message)
        case sdk_models.AuthenticationFailedException() | sdk_models.ForbiddenException():
            return ClearinghouseAccessDeniedError(message)
        case sdk_models.NotFoundException():
            return ClearinghouseNotFoundError(message)
        case sdk_models.TooManyRequestsException():
            return ClearinghouseRateLimitedError(message)
        case sdk_models.ConflictException():
            return ClearinghouseInFlightError(
                message, retry_after=getattr(exc, "retry_after", None)
            )
        case _:
            if not isinstance(exc, CallError):
                # A transport failure is ordinary; anything else reaching here
                # is a shape this translation has never seen, and the only
                # record of it would otherwise be a generic "unavailable".
                logger.warning("clearinghouse_sdk_error_untranslated type=%s", type(exc).__name__)
            return ClearinghouseUnavailableError(message)
