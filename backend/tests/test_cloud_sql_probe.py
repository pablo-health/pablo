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
        assert collectors._cloud_sql_summary(outputs)["audit_logs_table_present"] == "true"

    def test_reports_missing_when_dt_is_empty(self) -> None:
        """Confirms the original PABLO-001 false-positive path: with the
        old search_path, \\dt returned 'Did not find any relations' and
        the summary set present=false even though practice.audit_logs
        actually existed."""
        outputs = [("tables", _DT_OUTPUT_EMPTY, "")]
        assert collectors._cloud_sql_summary(outputs)["audit_logs_table_present"] == "false"

    def test_captures_row_count_single_schema(self) -> None:
        """Single-tenant deployment shape: just `practice` exists."""
        outputs = [
            ("tables", _DT_OUTPUT_WITH_PRACTICE, ""),
            ("audit_logs_row_count_24h__practice", " count\n-------\n    42\n", ""),
        ]
        summary = collectors._cloud_sql_summary(outputs, ["practice"])
        assert summary["audit_logs_row_count_24h"] == "42"
        assert summary["audit_logs_per_schema_24h"] == "practice=42"
        assert summary["practice_schema_count"] == "1"

    def test_captures_row_count_sums_across_tenants(self) -> None:
        """Multi-tenant deployment shape: the summary's total field is
        the sum of per-schema counts, and the breakdown is preserved
        so the LLM can spot a single-tenant outage."""
        outputs = [
            ("tables", _DT_OUTPUT_WITH_PRACTICE, ""),
            ("audit_logs_row_count_24h__practice", " count\n-------\n     0\n", ""),
            ("audit_logs_row_count_24h__practice_alpha", " count\n-------\n   100\n", ""),
            ("audit_logs_row_count_24h__practice_beta", " count\n-------\n     7\n", ""),
        ]
        summary = collectors._cloud_sql_summary(
            outputs, ["practice", "practice_alpha", "practice_beta"]
        )
        assert summary["audit_logs_row_count_24h"] == "107"
        # Per-schema breakdown sorted alphabetically for diff stability.
        assert (
            summary["audit_logs_per_schema_24h"] == "practice=0,practice_alpha=100,practice_beta=7"
        )
        assert summary["practice_schema_count"] == "3"

    def test_flags_permission_denied(self) -> None:
        outputs = [("tables", "", "ERROR: permission denied for table audit_logs")]
        summary = collectors._cloud_sql_summary(outputs)
        assert summary["permission_denied_observed"] == "true"


class TestAuditMutability:
    """The audit-mutability probe flags an operation only when the app role
    BOTH holds the privilege AND no BEFORE trigger blocks it — crediting both
    enforcement models (privilege-revoke for the managed build, trigger for
    OSS self-host), and treating TRUNCATE as its own axis (a row-level
    UPDATE/DELETE trigger doesn't fire on TRUNCATE)."""

    @staticmethod
    def _run_audit_checks(mutability_row: str) -> list[dict[str, str]]:
        # First _run = audit_mutability — six booleans:
        #   upd_priv|del_priv|trunc_priv|upd_blocked|del_blocked|trunc_blocked
        # second _run = audit_fk_cascade (none here).
        side_effects = [
            (0, mutability_row, "", 0.0),
            (0, "", "", 0.0),
        ]
        with patch.object(collectors, "_run", side_effect=side_effects):
            return collectors._audit_integrity_checks(
                port="15433", env={}, schemas=["practice"], lines=[]
            )

    def test_managed_posture_clean_all_revoked(self) -> None:
        # Privilege-based model: UPDATE/DELETE/TRUNCATE revoked, no triggers.
        assert self._run_audit_checks("f|f|f|f|f|f") == []

    def test_oss_trigger_model_clean(self) -> None:
        # OSS self-host: app role owns the table (keeps all privileges) but a
        # BEFORE UPDATE/DELETE/TRUNCATE trigger blocks every op.
        assert self._run_audit_checks("t|t|t|t|t|t") == []

    def test_oss_trigger_without_truncate_cover_flags_only_truncate(self) -> None:
        # OSS today: BEFORE UPDATE/DELETE trigger present, but TRUNCATE neither
        # revoked nor trigger-covered — the one real gap.
        ids = {f["id"] for f in self._run_audit_checks("t|t|t|t|t|f")}
        assert ids == {"audit-logs-truncatable:practice"}

    def test_privilege_without_trigger_flags_all(self) -> None:
        # Genuinely unprotected: privileges held, no triggers.
        ids = {f["id"] for f in self._run_audit_checks("t|t|t|f|f|f")}
        assert ids == {
            "audit-logs-updatable:practice",
            "audit-logs-deletable:practice",
            "audit-logs-truncatable:practice",
        }
        assert all(f["severity"] == "MEDIUM" for f in self._run_audit_checks("t|t|t|f|f|f"))

    def test_truncate_revoked_clears_truncatable_even_without_trigger(self) -> None:
        # Managed TRUNCATE handling is a revoke, not a trigger.
        assert self._run_audit_checks("t|t|f|t|t|f") == []
