# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Regression tests for collect_cloud_sql search_path handling.

The `pablo` DB role defaults to `search_path = "$user", public`, which omits
the tenant schema where `audit_logs` actually lives. When the probe runs psql
without overriding that, `\\dt` and `SELECT ... FROM audit_logs` miss the
table entirely and the run falsely reports PABLO-001 HIGH
("missing audit_logs"). The probe must force the same search_path the app
uses per session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from app.jobs import pentest_collectors as collectors

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


EXPECTED_PGOPTIONS = "-c search_path=practice,platform,public"


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def targets() -> collectors.Targets:
    return collectors.Targets(
        project_id="pablo-test",
        sql_connection="pablo-test:us-central1:pablo",
    )


class _CapturingRun:
    """Stands in for pentest_collectors._run.

    First call = gcloud secrets fetch (returns canned password). Remaining
    calls = psql invocations; each records the env it was given so tests
    can assert PGOPTIONS made it through.
    """

    def __init__(self, tables_stdout: str = "") -> None:
        self.tables_stdout = tables_stdout
        self.psql_calls: list[dict[str, str]] = []
        self._call_count = 0

    def __call__(
        self,
        cmd: list[str],
        *,
        timeout: int,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> tuple[int, str, str, int]:
        self._call_count += 1
        if cmd[0] == "gcloud":
            return (0, "dbpw-secret", "", 1)
        # psql — every call should carry PGOPTIONS
        assert cmd[0] == "psql"
        self.psql_calls.append(dict(env or {}))
        # The third psql query is `\dt` — return schema-qualified output
        # to prove the summary picks audit_logs up.
        query = cmd[-1]
        if query == r"\dt":
            return (0, self.tables_stdout, "", 1)
        return (0, "1\n", "", 1)


@pytest.fixture
def fake_proxy() -> Iterator[MagicMock]:
    proxy = MagicMock()
    proxy.wait.return_value = 0
    with patch.object(collectors.subprocess, "Popen", return_value=proxy) as p:
        yield p


@pytest.fixture
def tools_present() -> Iterator[None]:
    with patch.object(collectors, "_tool_available", return_value=True):
        yield


@pytest.fixture
def no_sleep() -> Iterator[None]:
    with patch.object(collectors.time, "sleep", return_value=None):
        yield


_DT_OUTPUT_WITH_PRACTICE = (
    "          List of relations\n"
    " Schema   |    Name    | Type  | Owner \n"
    "----------+------------+-------+-------\n"
    " practice | audit_logs | table | pablo \n"
    " practice | patients   | table | pablo \n"
)

_DT_OUTPUT_EMPTY = "Did not find any relations.\n"


class TestCollectCloudSqlSearchPath:
    def test_psql_env_carries_tenant_search_path(
        self,
        bundle_dir: Path,
        targets: collectors.Targets,
        fake_proxy: MagicMock,
        tools_present: None,
        no_sleep: None,
    ) -> None:
        """Every psql call must set PGOPTIONS so `pablo` starts its session
        with search_path that includes the tenant schema. Without this,
        \\dt misses practice.audit_logs."""
        cap = _CapturingRun(tables_stdout=_DT_OUTPUT_WITH_PRACTICE)
        with patch.object(collectors, "_run", side_effect=cap):
            artifact = collectors.collect_cloud_sql(bundle_dir, targets)

        assert artifact.status == "ok"
        assert cap.psql_calls, "no psql calls recorded"
        for env in cap.psql_calls:
            assert env.get("PGOPTIONS") == EXPECTED_PGOPTIONS
            assert env.get("PGPASSWORD") == "dbpw-secret"

    def test_summary_flags_audit_logs_when_schema_qualified(
        self,
        bundle_dir: Path,
        targets: collectors.Targets,
        fake_proxy: MagicMock,
        tools_present: None,
        no_sleep: None,
    ) -> None:
        """End-to-end: with the search_path fix, the probe's summary field
        reports audit_logs_table_present=true for schema-qualified output."""
        cap = _CapturingRun(tables_stdout=_DT_OUTPUT_WITH_PRACTICE)
        with patch.object(collectors, "_run", side_effect=cap):
            artifact = collectors.collect_cloud_sql(bundle_dir, targets)

        assert artifact.summary.get("audit_logs_table_present") == "true"


class TestCloudSqlSummaryShape:
    """Direct tests on _cloud_sql_summary — cheap coverage, no subprocess."""

    def test_detects_audit_logs_in_schema_qualified_dt(self) -> None:
        outputs = [("tables", _DT_OUTPUT_WITH_PRACTICE, "")]
        assert (
            collectors._cloud_sql_summary(outputs)["audit_logs_table_present"]
            == "true"
        )

    def test_reports_missing_when_dt_is_empty(self) -> None:
        """Confirms the original PABLO-001 false-positive path: with the
        old search_path, \\dt returned 'Did not find any relations' and
        the summary set present=false even though practice.audit_logs
        actually existed."""
        outputs = [("tables", _DT_OUTPUT_EMPTY, "")]
        assert (
            collectors._cloud_sql_summary(outputs)["audit_logs_table_present"]
            == "false"
        )

    def test_captures_row_count(self) -> None:
        outputs = [
            ("tables", _DT_OUTPUT_WITH_PRACTICE, ""),
            ("audit_logs_row_count_24h", " count\n-------\n    42\n", ""),
        ]
        summary = collectors._cloud_sql_summary(outputs)
        assert summary["audit_logs_row_count_24h"] == "42"

    def test_flags_permission_denied(self) -> None:
        outputs = [("tables", "", "ERROR: permission denied for table audit_logs")]
        summary = collectors._cloud_sql_summary(outputs)
        assert summary["permission_denied_observed"] == "true"
