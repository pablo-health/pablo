# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What every other module in this suite silently depends on (PABLO-1vep).

Each integration module is guarded by
``skipif(not DATABASE_URL or DATABASE_BACKEND != "postgres")``. That guard is
load-bearing and invisible: when it is wrong, the suite does not fail — it
reports `s` for every test and exits 0, which is how PABLO-1vep hid.

So the bootstrap's output gets asserted directly, by the one module here that
is deliberately **not** skipif-guarded. If `conftest.pytest_configure` stops
advertising a Postgres backend — by either of its two paths, the testcontainers
one or the bring-your-own-database early return — this fails loudly instead of
turning the whole suite into skips.

Deliberately cheap: it reads environment variables and opens nothing. It runs
in a couple of milliseconds whether or not a container was needed, so nothing
about it discourages running the suite.
"""

from __future__ import annotations

import os


def test_bootstrap_advertises_a_database() -> None:
    """``DATABASE_URL`` is set by the time tests run, by either path."""
    assert os.environ.get("DATABASE_URL"), (
        "conftest.pytest_configure left DATABASE_URL unset — every module in "
        "this suite would skip, and the run would still exit 0"
    )


def test_bootstrap_advertises_the_postgres_backend() -> None:
    """``DATABASE_BACKEND`` is ``postgres``, which is what the skipifs require.

    This is the assertion that PABLO-1vep needed and did not have. The variable
    was set on the testcontainers path only, so supplying a real DATABASE_URL
    skipped the entire suite in silence.
    """
    assert os.environ.get("DATABASE_BACKEND") == "postgres", (
        "conftest.pytest_configure did not set DATABASE_BACKEND=postgres, so "
        "every skipif in this suite fires and the whole suite reports skipped "
        "while exiting 0 (PABLO-1vep). Got: "
        f"{os.environ.get('DATABASE_BACKEND')!r}"
    )


def test_the_placeholder_database_url_never_reaches_a_real_run() -> None:
    """The unit suite's stand-in must never be what this suite ran against.

    Reaching here with the marker set would mean the guard in
    ``conftest.pytest_configure`` did not fire and the suite is pointed at a
    database that does not exist.
    """
    from tests.placeholder_db import database_url_is_placeholder  # noqa: PLC0415

    assert not database_url_is_placeholder(), (
        "the integration suite is running against the unit suite's placeholder "
        "DATABASE_URL — the guard in conftest.pytest_configure should have "
        "refused this run"
    )
