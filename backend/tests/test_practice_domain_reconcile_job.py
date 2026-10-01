# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The domain reconciler job's configuration: off unless fully set.

A sweep against a real database is in
``tests_integration/database/test_practice_domain_reconciler_db.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from app.jobs import practice_domain_reconcile
from app.services.practice_domain_cloud import ServingConfig, serving_configured
from app.settings import get_settings

from tests.practice_domain_serving_fake import FakeDomainServing

if TYPE_CHECKING:
    from collections.abc import Iterator

SERVING_ENV = {
    "PRACTICE_DOMAIN_SERVING_PROJECT": "example-project",
    "PRACTICE_DOMAIN_CERTIFICATE_MAP": "sites-cert-map",
    "PRACTICE_DOMAIN_URL_MAP": "sites-urlmap",
    "PRACTICE_DOMAIN_PATH_MATCHER": "sites-paths",
}


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (*SERVING_ENV, "PRACTICE_DOMAIN_CNAME_TARGET", "PRACTICE_DOMAIN_APEX_IPS"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def test_unconfigured_the_job_says_so_and_does_nothing(caplog: pytest.LogCaptureFixture) -> None:
    serving = FakeDomainServing()

    with caplog.at_level(logging.INFO):
        assert practice_domain_reconcile.run(serving) == 0

    assert "practice_domain_reconcile_disabled" in caplog.text
    assert serving.calls == []
    assert serving_configured(get_settings()) is False


def test_a_half_set_configuration_fails_and_names_what_is_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _set(monkeypatch, PRACTICE_DOMAIN_SERVING_PROJECT="example-project")

    assert practice_domain_reconcile.run(FakeDomainServing()) == 1

    assert "practice_domain_url_map" in caplog.text
    # Removal still waits for the job, so a host removed meanwhile keeps its
    # serving until the job can take it down.
    assert serving_configured(get_settings()) is True


def test_serving_needs_something_to_check_a_host_points_at(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _set(monkeypatch, **SERVING_ENV)

    assert practice_domain_reconcile.run(FakeDomainServing()) == 1
    assert "practice_domain_cname_target" in caplog.text


def test_the_full_configuration_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, **{name: f" {value} " for name, value in SERVING_ENV.items()})

    assert ServingConfig.from_settings(get_settings()) == ServingConfig(
        project="example-project",
        certificate_map="sites-cert-map",
        url_map="sites-urlmap",
        path_matcher="sites-paths",
    )
