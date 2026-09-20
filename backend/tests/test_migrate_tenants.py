# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the pure pieces of the tenant fan-out CLI.

The actual ``alembic upgrade head`` call requires a live database — that
path is exercised in ``tests_integration/database/test_migrate_tenants.py``.
Here we verify the iteration logic, status aggregation, and exit-code
reduction with a fake runner.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, cast

from app.db import RlsReconcileCounts
from app.db.migrate_tenants import (
    TenantResult,
    TenantStatus,
    aggregate_exit_code,
    fan_out,
    list_active_practice_registry,
    summarize,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def _runner(plan: dict[str, TenantStatus]):
    def _run(_engine, schema: str) -> TenantResult:
        return TenantResult(schema, plan[schema], detail="fake")

    return _run


def _reconciler(calls: list[str], *, raises_on: str | None = None):
    """A stand-in for the RLS reconcile that records the schemas it saw.

    The real one needs a database. What the fan-out owes callers is that it
    runs per schema and that a failure changes the tenant's status, and both
    are decidable without one.
    """

    def _reconcile(engine: Engine, schema: str) -> RlsReconcileCounts:
        calls.append(schema)
        if schema == raises_on:
            raise RuntimeError("policy apply blew up")
        return RlsReconcileCounts(applied=42, skipped=1)

    return _reconcile


def _no_reconcile(engine: Engine, schema: str) -> RlsReconcileCounts:
    return RlsReconcileCounts(applied=0, skipped=0)


def test_fan_out_invokes_runner_per_schema_in_order() -> None:
    plan = {
        "practice_a": TenantStatus.SUCCESS,
        "practice_b": TenantStatus.ALREADY_AT_HEAD,
        "practice_c": TenantStatus.FAILED,
    }
    results = fan_out(
        engine=cast("Engine", None),
        schemas=list(plan),
        runner=_runner(plan),
        max_workers=1,
        reconciler=_no_reconcile,
    )

    assert [r.schema for r in results] == ["practice_a", "practice_b", "practice_c"]
    assert [r.status for r in results] == list(plan.values())


def test_fan_out_parallel_preserves_input_order() -> None:
    # Even with parallel execution, results must be in input order so
    # callers can correlate by index.
    plan = {f"practice_{i:02d}": TenantStatus.SUCCESS for i in range(10)}
    results = fan_out(
        engine=cast("Engine", None),
        schemas=list(plan),
        runner=_runner(plan),
        max_workers=4,
        reconciler=_no_reconcile,
    )
    assert [r.schema for r in results] == list(plan)


def test_fan_out_parallel_actually_runs_concurrently() -> None:
    # A runner that sleeps 200ms per call should finish ~5x faster with
    # 5 workers than serially. Lower bound is generous to avoid CI flake.
    start_barrier = threading.Barrier(5)

    def _slow_runner(_engine, schema: str) -> TenantResult:
        start_barrier.wait(timeout=2.0)
        time.sleep(0.2)
        return TenantResult(schema, TenantStatus.SUCCESS, detail="slept")

    schemas = [f"practice_p{i}" for i in range(5)]
    t0 = time.monotonic()
    results = fan_out(
        engine=cast("Engine", None),
        schemas=schemas,
        runner=_slow_runner,
        max_workers=5,
        reconciler=_no_reconcile,
    )
    elapsed = time.monotonic() - t0

    assert len(results) == 5
    assert all(r.status is TenantStatus.SUCCESS for r in results)
    # Serial would be 5 * 0.2 = 1.0s; parallel should be ~0.2s + overhead.
    assert elapsed < 0.8, f"fan_out did not run in parallel (elapsed={elapsed:.2f}s)"


def test_fan_out_continues_past_failures() -> None:
    plan = {
        "practice_a": TenantStatus.SUCCESS,
        "practice_bad": TenantStatus.FAILED,
        "practice_c": TenantStatus.SUCCESS,
    }
    results = fan_out(
        engine=cast("Engine", None),
        schemas=list(plan),
        runner=_runner(plan),
        max_workers=1,
        reconciler=_no_reconcile,
    )

    # All three were attempted — one bad tenant must not abort the rest.
    assert [r.schema for r in results] == list(plan)


def test_fan_out_reconciles_rls_on_every_migrated_schema() -> None:
    """Including the schema that was already at head.

    That case is the one that matters: a table's registration can change
    with no revision beside it, so a practice already at head still needs
    today's policies applied. Reconciling only after a real upgrade would
    leave exactly the schemas nobody thought to look at.
    """
    plan = {
        "practice_a": TenantStatus.SUCCESS,
        "practice_b": TenantStatus.ALREADY_AT_HEAD,
    }
    seen: list[str] = []

    results = fan_out(
        engine=cast("Engine", None),
        schemas=list(plan),
        runner=_runner(plan),
        max_workers=1,
        reconciler=_reconciler(seen),
    )

    assert seen == ["practice_a", "practice_b"]
    assert all(r.ok for r in results)


def test_fan_out_skips_reconcile_for_a_failed_upgrade() -> None:
    """A schema whose DDL did not land is not one to apply policies to."""
    plan = {"practice_a": TenantStatus.SUCCESS, "practice_bad": TenantStatus.FAILED}
    seen: list[str] = []

    fan_out(
        engine=cast("Engine", None),
        schemas=list(plan),
        runner=_runner(plan),
        max_workers=1,
        reconciler=_reconciler(seen),
    )

    assert seen == ["practice_a"]


def test_fan_out_marks_a_failed_reconcile_as_a_failed_tenant() -> None:
    """The upgrade succeeding is not the whole job.

    A tenant whose policies could not be applied carries an unprotected or
    deny-all table, so it must not report success and must not leave the
    job exiting 0.
    """
    plan = {
        "practice_ok": TenantStatus.SUCCESS,
        "practice_norls": TenantStatus.ALREADY_AT_HEAD,
    }
    seen: list[str] = []

    results = fan_out(
        engine=cast("Engine", None),
        schemas=list(plan),
        runner=_runner(plan),
        max_workers=1,
        reconciler=_reconciler(seen, raises_on="practice_norls"),
    )
    by_schema = {r.schema: r for r in results}

    assert by_schema["practice_ok"].ok
    assert by_schema["practice_norls"].status is TenantStatus.FAILED
    assert "reconcile" in by_schema["practice_norls"].detail
    assert aggregate_exit_code(results) == 1


def test_aggregate_exit_code_zero_when_all_ok() -> None:
    results = [
        TenantResult("a", TenantStatus.SUCCESS),
        TenantResult("b", TenantStatus.ALREADY_AT_HEAD),
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


class _CapturingEngine:
    """Records the SQL a registry read issues, and returns no rows."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def connect(self) -> _CapturingEngine:
        return self

    def execute(self, statement: object) -> _CapturingEngine:
        self.statements.append(str(statement))
        return self

    def fetchall(self) -> list[tuple[str, str]]:
        return []

    def __enter__(self) -> _CapturingEngine:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def test_registry_excludes_synthetic_tenants_in_the_query() -> None:
    """The exclusion must reach SQL, not be applied to the rows afterwards.

    A caller that filtered after reading would still pay the per-tenant cost
    the exclusion exists to avoid, and — because callers slice the result to
    a maximum — synthetic rows would still consume that budget and crowd real
    practices out of the pass.
    """
    engine = _CapturingEngine()

    list_active_practice_registry(cast("Engine", engine), include_pentest=False)

    assert "is_pentest = FALSE" in engine.statements[0]


def test_registry_includes_every_tenant_by_default() -> None:
    """Some fan-outs must reach every schema that exists, so this is opt-in."""
    engine = _CapturingEngine()

    list_active_practice_registry(cast("Engine", engine))

    assert "is_pentest" not in engine.statements[0]
