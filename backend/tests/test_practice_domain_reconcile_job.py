# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The domain reconciler job: off unless fully set, idle unless there is work,
and how long a run keeps sweeping.

Sweeps against a real database are in
``tests_integration/database/test_practice_domain_reconciler_db.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest
from app.jobs import practice_domain_reconcile
from app.jobs.practice_domain_reconcile import (
    MAX_RUN,
    SWEEP_INTERVAL,
    keep_sweeping,
)
from app.services.practice_domain_cloud import ServingConfig, serving_configured
from app.services.practice_domain_reconciler import SweepReport
from app.settings import get_settings

from tests.practice_domain_serving_fake import FakeDomainServing

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator
    from contextlib import AbstractContextManager

    from app.models.practice_domain import HostStatus, PracticeDomain
    from app.services.practice_domain_cloud import DomainServing

SERVING_ENV = {
    "PRACTICE_DOMAIN_SERVING_PROJECT": "example-project",
    "PRACTICE_DOMAIN_CERTIFICATE_MAP": "sites-cert-map",
    "PRACTICE_DOMAIN_URL_MAP": "sites-urlmap",
    "PRACTICE_DOMAIN_PATH_MATCHER": "sites-paths",
    "PRACTICE_DOMAIN_CNAME_TARGET": "sites.example.net",
}


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (*SERVING_ENV, "PRACTICE_DOMAIN_APEX_IPS"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


class _Factory:
    """Counts how often the cloud seam is built."""

    def __init__(self) -> None:
        self.built: list[ServingConfig] = []
        self.serving = FakeDomainServing()

    def __call__(self, config: ServingConfig) -> DomainServing:
        self.built.append(config)
        return self.serving


class _EmptyStore:
    """A store with no hosts at all, which says whether any is in progress."""

    def __init__(self, *, in_progress: bool) -> None:
        self._in_progress = in_progress
        self.asked = 0

    def any_in_progress(self) -> bool:
        self.asked += 1
        return self._in_progress

    def all_hosts(self) -> list[PracticeDomain]:
        return []

    def retired_practices(self, practice_ids: Collection[str]) -> set[str]:
        return set()

    def practice(self, practice_id: str) -> AbstractContextManager[Any]:
        raise AssertionError("no practice is visited when there are no hosts")


def _no_sleep(_seconds: float) -> None:
    raise AssertionError("a run with nothing in progress never waits")


# --- configuration ------------------------------------------------------------


def test_unconfigured_the_job_says_so_and_does_nothing(caplog: pytest.LogCaptureFixture) -> None:
    factory = _Factory()

    with caplog.at_level(logging.INFO):
        assert practice_domain_reconcile.run([], serving_factory=factory) == 0

    assert "practice_domain_reconcile_disabled" in caplog.text
    assert factory.built == []
    assert serving_configured(get_settings()) is False


def test_a_half_set_configuration_fails_and_names_what_is_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _set(monkeypatch, PRACTICE_DOMAIN_SERVING_PROJECT="example-project")

    assert practice_domain_reconcile.run([], serving_factory=_Factory()) == 1

    assert "practice_domain_url_map" in caplog.text
    # Removal still waits for the job, so a host removed meanwhile keeps its
    # serving until the job can take it down.
    assert serving_configured(get_settings()) is True


def test_serving_needs_something_to_check_a_host_points_at(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _set(monkeypatch, **{k: v for k, v in SERVING_ENV.items() if "CNAME" not in k})

    assert practice_domain_reconcile.run([], serving_factory=_Factory()) == 1
    assert "practice_domain_cname_target" in caplog.text


def test_the_full_configuration_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, **{name: f" {value} " for name, value in SERVING_ENV.items()})

    assert ServingConfig.from_settings(get_settings()) == ServingConfig(
        project="example-project",
        certificate_map="sites-cert-map",
        url_map="sites-urlmap",
        path_matcher="sites-paths",
    )


# --- the fast path --------------------------------------------------------------


def test_with_nothing_in_progress_a_run_ends_before_touching_the_cloud(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _set(monkeypatch, **SERVING_ENV)
    factory, store = _Factory(), _EmptyStore(in_progress=False)

    with caplog.at_level(logging.INFO):
        code = practice_domain_reconcile.run(
            [], serving_factory=factory, store=store, sleep=_no_sleep
        )

    assert code == 0
    assert store.asked == 1
    assert factory.built == []
    assert factory.serving.calls == []
    assert "practice_domain_reconcile_idle" in caplog.text


def test_the_daily_recheck_sweeps_even_with_nothing_in_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set(monkeypatch, **SERVING_ENV)
    factory = _Factory()

    code = practice_domain_reconcile.run(
        ["--recheck"],
        serving_factory=factory,
        store=_EmptyStore(in_progress=False),
        sleep=_no_sleep,
    )

    assert code == 0
    assert len(factory.built) == 1


# --- how long a run keeps sweeping ------------------------------------------------


class _Clock:
    """Time that moves only when the run sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _report(
    statuses: dict[str, HostStatus],
    *,
    written: int = 0,
    awaiting_practice: set[str] | None = None,
) -> SweepReport:
    return SweepReport(
        written=written,
        statuses=dict(statuses),
        awaiting_practice=awaiting_practice or set(),
    )


def _sweeps(*reports: SweepReport) -> Any:
    """A sweep that answers *reports* in turn, then the last one forever."""
    queue = list(reports)

    def sweep() -> SweepReport:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return sweep


def test_a_run_stops_once_nothing_is_in_progress() -> None:
    clock = _Clock()

    result = keep_sweeping(
        _sweeps(
            _report({"a.example": "verifying"}, written=1),
            _report({"a.example": "active"}, written=1),
        ),
        clock=clock,
        sleep=clock.sleep,
    )

    assert (result.sweeps, result.stopped) == (2, "done")
    assert clock.slept == [SWEEP_INTERVAL.total_seconds()]


def test_a_run_waiting_on_the_issuer_keeps_going_until_max_run() -> None:
    clock = _Clock()

    result = keep_sweeping(
        _sweeps(_report({"a.example": "verifying"})), clock=clock, sleep=clock.sleep
    )

    assert result.stopped == "max_run"
    assert clock.now < MAX_RUN.total_seconds()
    assert clock.now + SWEEP_INTERVAL.total_seconds() >= MAX_RUN.total_seconds()
    assert result.sweeps == len(clock.slept) + 1


def test_a_run_waiting_only_on_practices_stops_after_two_quiet_sweeps() -> None:
    clock = _Clock()
    waiting = {"a.example": "verifying", "b.example": "verifying"}

    result = keep_sweeping(
        _sweeps(
            _report(waiting, written=2, awaiting_practice=set(waiting)),
            _report(waiting, awaiting_practice=set(waiting)),
            _report(waiting, awaiting_practice=set(waiting)),
        ),
        clock=clock,
        sleep=clock.sleep,
    )

    assert (result.sweeps, result.stopped) == (3, "waiting_on_practice")


def test_one_host_waiting_on_the_issuer_keeps_the_run_alive() -> None:
    clock = _Clock()

    result = keep_sweeping(
        _sweeps(
            _report(
                {"a.example": "verifying", "b.example": "verifying"},
                awaiting_practice={"a.example"},
            )
        ),
        clock=clock,
        sleep=clock.sleep,
    )

    assert result.stopped == "max_run"


def test_a_host_being_removed_keeps_the_run_alive() -> None:
    clock = _Clock()

    result = keep_sweeping(
        _sweeps(
            _report({"a.example": "removing"}),
            _report({"a.example": "removing"}),
            _report({}, written=0),
        ),
        clock=clock,
        sleep=clock.sleep,
    )

    assert (result.sweeps, result.stopped) == (3, "done")
