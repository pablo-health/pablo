# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Calling the vendor's async SDK from synchronous code.

The properties here are the ones that would otherwise fail in production
rather than in a test run. A client is bound to the event loop it was built
on, so the loop has to outlive the clients and be the same one every time —
including for callers on a worker thread, which is how the claims pipeline
runs. And a call that never returns has to give the calling thread back,
because in a threadpool a pinned thread is how a service stops answering.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from app.claims.clearinghouse import (
    ClearinghouseAccessDeniedError,
    ClearinghouseInFlightError,
    ClearinghouseNotFoundError,
    ClearinghouseRateLimitedError,
    ClearinghouseUnavailableError,
    ClearinghouseValidationError,
)
from app.claims.credentials import ClearinghouseCredentials
from app.claims.sdk_runtime import (
    run_on_sdk_loop,
    sdk_loop_running,
    shutdown_sdk_loop,
    translate_sdk_error,
)
from app.claims.stedi_sdk import _clients, client_for, close_clients, shutdown_sdk
from smithy_core.exceptions import CallError
from stedi import models as sdk_models


def _credentials(api_key: str = "test_placeholder", base_url: str | None = None):
    return ClearinghouseCredentials(api_key=api_key, mode="test", base_url=base_url)


@pytest.fixture(autouse=True)
def _clean_runtime():
    """Each test gets its own loop and cache; nothing leaks between them."""
    yield
    run_on_sdk_loop(close_clients())
    shutdown_sdk_loop()


class TestTheOwnedLoop:
    async def _thread_id(self) -> int:
        return threading.get_ident()

    def test_work_runs_off_the_calling_thread(self) -> None:
        assert run_on_sdk_loop(self._thread_id()) != threading.get_ident()

    def test_every_call_lands_on_the_same_loop(self) -> None:
        async def loop_id() -> int:
            return id(asyncio.get_running_loop())

        assert run_on_sdk_loop(loop_id()) == run_on_sdk_loop(loop_id())

    def test_a_call_from_a_worker_thread_works(self) -> None:
        """The claims pipeline does its work in a thread, so this is its path."""
        seen: list[int] = []

        def in_thread() -> None:
            seen.append(run_on_sdk_loop(self._thread_id()))

        worker = threading.Thread(target=in_thread)
        worker.start()
        worker.join(timeout=10)

        assert len(seen) == 1

    def test_a_call_that_never_returns_gives_the_thread_back(self) -> None:
        async def forever() -> None:
            await asyncio.sleep(60)

        with pytest.raises(ClearinghouseUnavailableError, match="did not answer"):
            run_on_sdk_loop(forever(), timeout=0.05)

    def test_the_loop_restarts_after_shutdown(self) -> None:
        async def one() -> int:
            return 1

        assert run_on_sdk_loop(one()) == 1
        shutdown_sdk_loop()
        assert run_on_sdk_loop(one()) == 1


class TestTheClientCache:
    def test_the_same_credentials_share_one_client(self) -> None:
        first = run_on_sdk_loop(client_for(_credentials()))
        second = run_on_sdk_loop(client_for(_credentials()))

        assert first is second

    def test_a_redeployed_key_is_not_served_the_old_client(self) -> None:
        """The credential provider re-reads settings so a new key takes effect.

        A cache that ignored the key would keep authenticating as the old one
        — silently, and for as long as the process lived.
        """
        first = run_on_sdk_loop(client_for(_credentials(api_key="test_first")))
        second = run_on_sdk_loop(client_for(_credentials(api_key="test_second")))

        assert first is not second

    def test_a_different_origin_is_a_different_client(self) -> None:
        default = run_on_sdk_loop(client_for(_credentials()))
        stand_in = run_on_sdk_loop(
            client_for(_credentials(base_url="http://fake-clearinghouse:8080"))
        )

        assert default is not stand_in

    def test_shutdown_does_nothing_when_nothing_used_the_sdk(self) -> None:
        """Most deployments file no claims; shutdown must not start a loop to stop one."""
        shutdown_sdk_loop()
        assert not sdk_loop_running()

        shutdown_sdk()

        assert not sdk_loop_running()

    def test_shutdown_closes_the_clients_and_the_loop(self) -> None:
        run_on_sdk_loop(client_for(_credentials()))
        assert sdk_loop_running()

        shutdown_sdk()

        assert not _clients
        assert not sdk_loop_running()

    def test_closing_empties_the_cache(self) -> None:
        run_on_sdk_loop(client_for(_credentials()))
        assert _clients

        run_on_sdk_loop(close_clients())

        assert not _clients


class TestErrorTranslation:
    """Callers catch this package's errors; the vendor's must not leak past."""

    @pytest.mark.parametrize(
        ("raised", "expected"),
        [
            (sdk_models.InvalidRequestException("bad"), ClearinghouseValidationError),
            (sdk_models.ContentTooLargeException("big"), ClearinghouseValidationError),
            (sdk_models.AuthenticationFailedException("who"), ClearinghouseAccessDeniedError),
            (sdk_models.ForbiddenException("no"), ClearinghouseAccessDeniedError),
            (sdk_models.NotFoundException("gone"), ClearinghouseNotFoundError),
            (sdk_models.TooManyRequestsException("slow"), ClearinghouseRateLimitedError),
            (sdk_models.ConflictException("busy"), ClearinghouseInFlightError),
            (sdk_models.InternalServerException("boom"), ClearinghouseUnavailableError),
            (CallError("transport"), ClearinghouseUnavailableError),
        ],
    )
    def test_each_vendor_error_becomes_one_of_ours(
        self, raised: Exception, expected: type[Exception]
    ) -> None:
        assert isinstance(translate_sdk_error(raised), expected)

    def test_an_unrecognised_error_still_becomes_one_of_ours(self) -> None:
        """Never let a vendor type escape a caller's ``except ClearinghouseError``."""
        assert isinstance(
            translate_sdk_error(ValueError("surprise")), ClearinghouseUnavailableError
        )

    def test_a_conflict_carries_the_vendors_retry_hint(self) -> None:
        conflict = sdk_models.ConflictException("busy")
        conflict.retry_after = 12.0

        translated = translate_sdk_error(conflict)

        assert isinstance(translated, ClearinghouseInFlightError)
        assert translated.retry_after == 12.0
