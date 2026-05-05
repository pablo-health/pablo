# Copyright (c) 2026 Pablo Health, LLC. All rights reserved under AGPL-3.0.

"""Unit tests for the audit-log retention purge cron (THERAPY-agx)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from app.jobs import audit_retention_cron


def test_parse_as_of_defaults_to_now() -> None:
    freeze = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    with patch("app.jobs.audit_retention_cron.datetime") as dt_mod:
        dt_mod.UTC = UTC
        dt_mod.now.return_value = freeze
        got = audit_retention_cron._parse_as_of(None)
    assert got == freeze


def test_parse_as_of_iso_string_with_z() -> None:
    got = audit_retention_cron._parse_as_of("2026-02-01T00:00:00Z")
    assert got == datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)


def test_parse_as_of_naive_iso_treated_as_utc() -> None:
    got = audit_retention_cron._parse_as_of("2026-02-01T00:00:00")
    assert got == datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)


def test_dry_run_does_not_delete_and_returns_zero() -> None:
    """Dry-run path issues COUNT(*) only and returns 0 without DELETE."""
    schemas = ["practice_alpha", "practice_beta"]
    mock_engine = MagicMock()

    with (
        patch("app.jobs.audit_retention_cron.get_engine", return_value=mock_engine),
        patch(
            "app.jobs.audit_retention_cron.list_active_tenant_schemas",
            return_value=schemas,
        ),
        patch(
            "app.jobs.audit_retention_cron._count_expired",
            return_value=3,
        ) as count_mock,
        patch(
            "app.jobs.audit_retention_cron._delete_expired",
            side_effect=AssertionError("must not delete in dry-run"),
        ) as delete_mock,
    ):
        rc = audit_retention_cron.run(["--dry-run"])

    assert rc == 0
    assert count_mock.call_count == len(schemas)
    delete_mock.assert_not_called()


def test_multi_schema_fan_out_calls_delete_per_schema() -> None:
    schemas = ["practice_alpha", "practice_beta", "practice_gamma"]
    mock_engine = MagicMock()

    with (
        patch("app.jobs.audit_retention_cron.get_engine", return_value=mock_engine),
        patch(
            "app.jobs.audit_retention_cron.list_active_tenant_schemas",
            return_value=schemas,
        ),
        patch(
            "app.jobs.audit_retention_cron._delete_expired",
            return_value=5,
        ) as delete_mock,
    ):
        rc = audit_retention_cron.run([])

    assert rc == 0
    assert delete_mock.call_count == len(schemas)
    called_schemas = [call.args[1] for call in delete_mock.call_args_list]
    assert called_schemas == schemas


def test_expires_at_filter_passes_as_of_to_query() -> None:
    """The SQL DELETE binds ``:as_of`` from the parsed CLI arg."""
    schemas = ["practice_alpha"]
    mock_engine = MagicMock()
    mock_result = MagicMock()
    mock_result.rowcount = 7

    mock_begin_cm = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_result
    mock_begin_cm.__enter__.return_value = mock_conn
    mock_begin_cm.__exit__.return_value = None
    mock_engine.begin.return_value = mock_begin_cm

    with (
        patch("app.jobs.audit_retention_cron.get_engine", return_value=mock_engine),
        patch(
            "app.jobs.audit_retention_cron.list_active_tenant_schemas",
            return_value=schemas,
        ),
    ):
        rc = audit_retention_cron.run(["--as-of", "2026-02-01T00:00:00Z"])

    assert rc == 0
    # First execute: SET search_path; second execute: DELETE with :as_of bind.
    delete_call = mock_conn.execute.call_args_list[-1]
    bind = delete_call.args[1]
    assert bind == {"as_of": datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)}
    assert "DELETE FROM audit_logs WHERE expires_at < :as_of" in str(delete_call.args[0])


def test_returns_one_on_db_error() -> None:
    schemas = ["practice_alpha"]
    mock_engine = MagicMock()

    with (
        patch("app.jobs.audit_retention_cron.get_engine", return_value=mock_engine),
        patch(
            "app.jobs.audit_retention_cron.list_active_tenant_schemas",
            return_value=schemas,
        ),
        patch(
            "app.jobs.audit_retention_cron._delete_expired",
            side_effect=RuntimeError("connection lost"),
        ),
    ):
        rc = audit_retention_cron.run([])

    assert rc == 1


def test_returns_one_when_engine_bootstrap_fails() -> None:
    with patch(
        "app.jobs.audit_retention_cron.get_engine",
        side_effect=RuntimeError("no DB url"),
    ):
        rc = audit_retention_cron.run([])
    assert rc == 1


def test_no_schemas_is_zero_rows_zero_exit() -> None:
    mock_engine = MagicMock()
    with (
        patch("app.jobs.audit_retention_cron.get_engine", return_value=mock_engine),
        patch(
            "app.jobs.audit_retention_cron.list_active_tenant_schemas",
            return_value=[],
        ),
    ):
        rc = audit_retention_cron.run([])
    assert rc == 0
