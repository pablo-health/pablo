# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the pure pieces of the tenant fan-out CLI.

The actual ``alembic upgrade head`` call requires a live database — that
path is exercised in ``tests_integration/database/test_migrate_tenants.py``.
Here we verify the iteration logic, status aggregation, and exit-code
reduction with a fake runner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.db.migrate_tenants import (
    TenantResult,
    TenantStatus,
    aggregate_exit_code,
    fan_out,
    summarize,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def _runner(plan: dict[str, TenantStatus]):
    def _run(_engine, schema: str) -> TenantResult:
        return TenantResult(schema, plan[schema], detail="fake")

    return _run


def test_fan_out_invokes_runner_per_schema_in_order() -> None:
    plan = {
        "practice_a": TenantStatus.SUCCESS,
        "practice_b": TenantStatus.ALREADY_AT_HEAD,
        "practice_c": TenantStatus.RECONCILED,
    }
    results = fan_out(engine=cast("Engine", None), schemas=list(plan), runner=_runner(plan))

    assert [r.schema for r in results] == ["practice_a", "practice_b", "practice_c"]
    assert [r.status for r in results] == list(plan.values())


def test_fan_out_continues_past_failures() -> None:
    plan = {
        "practice_a": TenantStatus.SUCCESS,
        "practice_bad": TenantStatus.FAILED,
        "practice_c": TenantStatus.SUCCESS,
    }
    results = fan_out(engine=cast("Engine", None), schemas=list(plan), runner=_runner(plan))

    # All three were attempted — one bad tenant must not abort the rest.
    assert [r.schema for r in results] == list(plan)


def test_aggregate_exit_code_zero_when_all_ok() -> None:
    results = [
        TenantResult("a", TenantStatus.SUCCESS),
        TenantResult("b", TenantStatus.ALREADY_AT_HEAD),
        TenantResult("c", TenantStatus.RECONCILED),
    ]
    assert aggregate_exit_code(results) == 0


def test_aggregate_exit_code_nonzero_on_any_failure() -> None:
    results = [
        TenantResult("a", TenantStatus.SUCCESS),
        TenantResult("b", TenantStatus.FAILED),
    ]
    assert aggregate_exit_code(results) == 1


def test_aggregate_exit_code_zero_for_empty_results() -> None:
    assert aggregate_exit_code([]) == 0


def test_summarize_lists_failed_schema_names() -> None:
    results = [
        TenantResult("a", TenantStatus.SUCCESS),
        TenantResult("b_bad", TenantStatus.FAILED, "boom"),
        TenantResult("c_bad", TenantStatus.FAILED, "boom"),
    ]
    line = summarize(results)
    assert "tenants=3" in line
    assert "failed=2" in line
    assert "b_bad" in line
    assert "c_bad" in line


def test_summarize_no_failed_section_when_clean() -> None:
    results = [TenantResult("a", TenantStatus.ALREADY_AT_HEAD)]
    assert "failed_schemas" not in summarize(results)
