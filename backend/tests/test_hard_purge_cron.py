# Copyright (c) 2026 Pablo Health, LLC. All rights reserved under AGPL-3.0.

"""Unit tests for the optional compliance hard-purge Cloud Run Job."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from app.jobs import hard_purge_cron


def test_run_exits_zero_when_no_stub_writer_registered() -> None:
    with (
        patch(
            "app.jobs.hard_purge_cron.get_compliance_retention_stub_writer",
            return_value=None,
        ),
        patch("app.jobs.hard_purge_cron.get_engine") as ge,
    ):
        assert hard_purge_cron.run([]) == 0
        ge.assert_not_called()


def test_parse_purge_before_defaults_to_roughly_now_minus_30_days() -> None:
    freeze = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    with patch("app.jobs.hard_purge_cron.datetime") as dt_mod:
        dt_mod.UTC = UTC
        dt_mod.now.return_value = freeze
        dt_mod.timedelta = timedelta
        got = hard_purge_cron._parse_purge_before(None)
    assert got == freeze - timedelta(days=30)


def test_parse_purge_before_iso_string() -> None:
    got = hard_purge_cron._parse_purge_before("2026-02-01T00:00:00Z")
    assert got == datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)


def test_run_exits_two_when_stub_writer_unsupported() -> None:
    mock_conn = MagicMock()
    stub_writer = MagicMock()
    stub_writer.is_supported.return_value = False

    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_conn
    mock_cm.__exit__.return_value = None
    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_cm

    with (
        patch(
            "app.jobs.hard_purge_cron.get_compliance_retention_stub_writer",
            return_value=stub_writer,
        ),
        patch("app.jobs.hard_purge_cron.get_engine", return_value=mock_engine),
        patch(
            "app.jobs.hard_purge_cron.list_active_practice_registry",
            side_effect=AssertionError("must not list tenants until supported"),
        ),
    ):
        assert hard_purge_cron.run([]) == 2


def test_run_dry_run_exits_zero_when_stub_writer_supported() -> None:
    mock_conn = MagicMock()
    stub_writer = MagicMock()
    stub_writer.is_supported.return_value = True

    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_conn
    mock_cm.__exit__.return_value = None
    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_cm

    with (
        patch(
            "app.jobs.hard_purge_cron.get_compliance_retention_stub_writer",
            return_value=stub_writer,
        ),
        patch("app.jobs.hard_purge_cron.get_engine", return_value=mock_engine),
        patch("app.jobs.hard_purge_cron.list_active_practice_registry", return_value=[]),
    ):
        assert hard_purge_cron.run(["--dry-run"]) == 0
