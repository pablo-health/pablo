# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The guard in ``create_standalone_session`` that keeps nested-pool sessions leaves.

Stand-in sessions here are plain objects, not MagicMocks: a MagicMock's
``in_transaction()`` is a truthy mock that the guard deliberately reads as
"holds nothing". The real-session counterpart is
``tests_integration/database/test_nested_pool_contract.py``.
"""

from __future__ import annotations

import gc
import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from app import db
from app.db import NESTED_OPEN_ALLOWED, NestedPoolContractError, create_standalone_session

if TYPE_CHECKING:
    from collections.abc import Iterator


class _Session:
    """Holds a connection until closed, as a real session does after its first query."""

    def __init__(self, pool: str) -> None:
        self.pool = pool
        self.open = True

    def in_transaction(self) -> bool:
        return self.open

    def close(self) -> None:
        self.open = False


@pytest.fixture
def pools() -> Iterator[None]:
    """Both factories hand out stand-ins labelled with their pool; the guard raises."""
    with (
        patch.object(db, "get_session_factory", return_value=lambda: _Session("main")),
        patch.object(db, "get_nested_session_factory", return_value=lambda: _Session("nested")),
    ):
        nested = db._nested_pool_sessions.set(())
        request = db._request_session.set(None)
        db.raise_on_nested_open()
        try:
            yield
        finally:
            db.raise_on_nested_open(False)
            db._request_session.reset(request)
            db._nested_pool_sessions.reset(nested)


@pytest.fixture
def request_holding(pools: None) -> Iterator[_Session]:
    """A request session bound to this context, past its first query."""
    held = _Session("main")
    token = db._request_session.set(held)  # type: ignore[arg-type]
    try:
        yield held
    finally:
        db._request_session.reset(token)


def _open_unreferenced_nested() -> None:
    """Open a nested-pool session and drop every reference this frame had to it."""
    session = create_standalone_session()
    assert session.pool == "nested"  # type: ignore[attr-defined]


@pytest.mark.parametrize("attempt", range(200))
def test_a_nested_open_inside_a_nested_session_raises_even_after_a_collection(
    request_holding: _Session, attempt: int
) -> None:
    """Nothing else points at the first session; a collection must not make the guard forget it.

    With weak references to the session object the guard lost it whenever a
    cyclic collection ran between the open and the check (pablo#1969's flake).
    """
    _open_unreferenced_nested()
    gc.collect()
    with pytest.raises(NestedPoolContractError, match="nested-pool session"):
        create_standalone_session()


def test_the_refused_open_takes_no_session(request_holding: _Session) -> None:
    opened: list[str] = []

    def factory() -> _Session:
        opened.append("nested")
        return _Session("nested")

    first = create_standalone_session()
    with (
        patch.object(db, "get_nested_session_factory", return_value=factory),
        pytest.raises(NestedPoolContractError),
    ):
        create_standalone_session()
    assert opened == []
    first.close()


def test_a_closed_nested_session_does_not_count_and_is_dropped(
    request_holding: _Session,
) -> None:
    first = create_standalone_session()
    first.close()
    second = create_standalone_session()
    assert db._nested_pool_sessions.get() == (second,)


def test_a_main_pool_open_beside_a_held_nested_session_raises_too(
    request_holding: _Session,
) -> None:
    """The request released its connection, the leaf did not: the next open is main-pool.

    Still refused. A caller holding a nested connection while waiting on the
    main pool can wait on requests that are themselves waiting on the nested
    pool.
    """
    create_standalone_session()
    request_holding.close()
    with pytest.raises(NestedPoolContractError):
        create_standalone_session()


def test_a_mock_session_holds_nothing(request_holding: _Session) -> None:
    """Unit tests hand out MagicMock sessions; their truthy ``in_transaction()`` is not a hold.

    Otherwise every unit test that opens two standalone sessions under a bound
    mock would count as a hit.
    """
    with patch.object(db, "get_nested_session_factory", return_value=MagicMock):
        create_standalone_session()
        create_standalone_session()


def test_main_pool_sessions_are_not_tracked(pools: None) -> None:
    """Outside a held request every open is main-pool, and two at once are fine (jobs)."""
    first = create_standalone_session()
    second = create_standalone_session()
    assert (first.pool, second.pool) == ("main", "main")  # type: ignore[attr-defined]
    assert db._nested_pool_sessions.get() == ()


def test_an_allowed_call_site_passes(request_holding: _Session) -> None:
    site = f"{__name__}:{test_an_allowed_call_site_passes.__name__}"
    create_standalone_session()
    with patch.dict(NESTED_OPEN_ALLOWED, {site: "reviewed in this test"}):
        create_standalone_session()


def test_the_allow_list_ships_empty() -> None:
    """Each entry needs its reason reviewed; one should not arrive unnoticed."""
    assert NESTED_OPEN_ALLOWED == {}


def test_the_call_site_is_the_first_frame_outside_app_db(request_holding: _Session) -> None:
    """``tenant_db_session`` lives in ``app.db``; a hit inside it names its caller."""
    create_standalone_session()
    with pytest.raises(NestedPoolContractError) as raised:
        create_standalone_session()
    assert f"{__name__}:test_the_call_site_is_the_first_frame_outside_app_db" in str(raised.value)


def test_a_hit_inside_an_app_db_submodule_names_that_helper_s_caller(
    request_holding: _Session,
) -> None:
    """A helper in ``app.db.*`` (``tenant_db_session``, say) is skipped like ``app.db`` itself."""
    helper_globals = {
        "__name__": "app.db.tenant_session",
        "create_standalone_session": create_standalone_session,
    }
    exec("def helper():\n    return create_standalone_session()\n", helper_globals)  # noqa: S102
    helper = helper_globals["helper"]
    create_standalone_session()
    with pytest.raises(NestedPoolContractError) as raised:
        helper()  # type: ignore[operator]
    assert str(raised.value).startswith(
        f"{__name__}:test_a_hit_inside_an_app_db_submodule_names_that_helper_s_caller "
    )


def test_a_server_logs_each_call_site_once(
    request_holding: _Session, caplog: pytest.LogCaptureFixture
) -> None:
    db.raise_on_nested_open(False)
    create_standalone_session()

    def opener() -> None:
        create_standalone_session()

    with (
        patch.object(db, "_nested_opens_logged", set()),
        caplog.at_level(logging.WARNING, logger="app.db"),
    ):
        opener()
        opener()
    hits = [
        r
        for r in caplog.records
        if r.name == "app.db" and getattr(r, "event", None) == "nested_pool_contract"
    ]
    assert len(hits) == 1
    assert hits[0].at == f"{__name__}:opener"  # type: ignore[attr-defined]
