# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the pytest streaming-body guard
(``backend.tests._streaming_body_guard``).

Self-tests cover three shapes the guard has to get right:
  1. No checkout, no false positives.
  2. A checkout inside the guarded block fails the block.
  3. A checkout outside the guarded block is invisible to the guard.

Plus a nesting-error case so a test author who forgets to release a
previous guard sees a clean error instead of silent corruption.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ._streaming_body_guard import (
    _record_checkout_during_guard,
    assert_no_db_checkouts,
)


def _simulate_checkout() -> None:
    """Fire the listener directly. Avoids spinning up an engine just to
    feed the event — the listener doesn't care about its args beyond
    the active-guard flag."""
    _record_checkout_during_guard(MagicMock(), MagicMock(), MagicMock())


def test_no_checkout_no_failure() -> None:
    with assert_no_db_checkouts("noop"):
        pass


def test_single_checkout_inside_block_fails() -> None:
    with pytest.raises(AssertionError) as exc, assert_no_db_checkouts("test-block"):
        _simulate_checkout()
    assert "test-block" in str(exc.value)
    assert "count=1" in str(exc.value)


def test_multiple_checkouts_summarized() -> None:
    with pytest.raises(AssertionError) as exc, assert_no_db_checkouts("test-block"):  # noqa: PT012
        for _ in range(5):
            _simulate_checkout()
    message = str(exc.value)
    assert "count=5" in message
    # Only the first 3 checkouts get full call-site stacks; remaining
    # are elided in a single summary line.
    assert "2 additional checkouts elided" in message


def test_checkout_outside_block_is_ignored() -> None:
    """Pre/post-block checkouts must not leak into the next guard run."""
    _simulate_checkout()
    with assert_no_db_checkouts("block"):
        pass  # no checkout inside
    _simulate_checkout()  # post-block checkout — also fine


def test_state_resets_between_guards() -> None:
    """If a prior block fired and was caught, the next block starts clean."""
    with pytest.raises(AssertionError), assert_no_db_checkouts("first"):
        _simulate_checkout()
    # Next block: should observe zero checkouts.
    with assert_no_db_checkouts("second"):
        pass


def test_nesting_raises_runtime_error() -> None:
    # SIM117/PT012: nesting is intentional — collapsing the inner
    # `with` into the same statement would put the inner __enter__
    # (which raises) in the same with-clause as pytest.raises, but the
    # outer must run __enter__ first to put the guard in the "active"
    # state for the nesting check to fire.
    with pytest.raises(RuntimeError, match="not re-entrant"):  # noqa: SIM117, PT012
        with assert_no_db_checkouts("outer"):
            with assert_no_db_checkouts("inner"):
                pass  # pragma: no cover — inner __enter__ raises before this
