# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""When the RLS reconcile may leave a table alone, decided without a database.

``enable_rls_on_schema`` rebuilds a table only when its live state differs
from what the rebuild would leave. Every case here is a way that comparison
could wrongly say "already right" — and so leave a dropped policy dropped, a
disabled table disabled, or a changed policy unchanged — plus the ones it must
not trip on (a policy some other pass owns). The live state is built by hand
in the catalog's shape; the real-database round trip is
``tests_integration/database/test_rls_template_parity.py``.
"""

from __future__ import annotations

from app.db import _plan_row_scoped_table, register_overlay_patient_scoped
from app.db.rls_state import (
    FINGERPRINT_PREFIX,
    LivePolicy,
    LiveRls,
    TablePlan,
    fingerprint,
    schema_neutral,
    table_matches,
)

_SCHEMA = "practice_unit"
_TABLE = "availability_rules"
_DEPARSED = "((user_id)::text = current_setting('app.current_user_id'::text, true))"


def _plan(table: str = _TABLE, columns: set[str] | None = None) -> TablePlan:
    return _plan_row_scoped_table(_SCHEMA, table, columns or {"id", "user_id"})


def _live(
    plan: TablePlan,
    *,
    flags: tuple[bool, bool] = (True, True),
    using: str = _DEPARSED,
    extra: dict[str, LivePolicy] | None = None,
    comment: str | None = "fingerprint",
) -> LiveRls:
    """The catalog as the reconcile would have left it, then varied."""
    policies = {name: LivePolicy("*", True, "public", using, using, None) for name in plan.policies}
    if comment == "fingerprint":
        value = fingerprint(_SCHEMA, plan, policies)
        policies = {name: p._replace(comment=value) for name, p in policies.items()}
    else:
        policies = {name: p._replace(comment=comment) for name, p in policies.items()}
    policies.update(extra or {})
    return LiveRls({_TABLE: flags}, {_TABLE: policies})


def test_a_table_left_as_the_rebuild_leaves_it_matches() -> None:
    plan = _plan()
    assert plan.policies == {"rls_user_isolation"}
    assert table_matches(_SCHEMA, _TABLE, plan, _live(plan))


def test_rls_disabled_or_unforced_does_not_match() -> None:
    plan = _plan()
    assert not table_matches(_SCHEMA, _TABLE, plan, _live(plan, flags=(False, False)))
    assert not table_matches(_SCHEMA, _TABLE, plan, _live(plan, flags=(True, False)))


def test_a_missing_policy_does_not_match() -> None:
    plan = _plan()
    live = _live(plan)
    live.policies[_TABLE].clear()
    assert not table_matches(_SCHEMA, _TABLE, plan, live)


def test_a_table_absent_from_the_catalog_does_not_match() -> None:
    assert not table_matches(_SCHEMA, _TABLE, _plan(), LiveRls({}, {}))


def test_a_policy_without_a_fingerprint_does_not_match() -> None:
    """Created by something other than the reconcile, so its text is unverified."""
    plan = _plan()
    assert not table_matches(_SCHEMA, _TABLE, plan, _live(plan, comment=None))


def test_a_policy_altered_after_it_was_fingerprinted_does_not_match() -> None:
    """``ALTER POLICY`` keeps the comment; the stored text is what changed."""
    plan = _plan()
    live = _live(plan)
    name = next(iter(plan.policies))
    live.policies[_TABLE][name] = live.policies[_TABLE][name]._replace(using="true")
    assert not table_matches(_SCHEMA, _TABLE, plan, live)


def test_a_stale_policy_under_a_managed_name_does_not_match() -> None:
    """A patient arm the table no longer has a registration for is shed by the rebuild."""
    plan = _plan()
    stale = LivePolicy("r", True, "public", "true", None, None)
    assert not table_matches(
        _SCHEMA, _TABLE, plan, _live(plan, extra={"rls_patient_self_read": stale})
    )


def test_a_policy_another_pass_owns_is_ignored() -> None:
    """The practice directory's read policy is not the table loop's to check."""
    plan = _plan()
    directory = LivePolicy("r", True, "pablo_practice_directory", "true", None, None)
    assert table_matches(
        _SCHEMA, _TABLE, plan, _live(plan, extra={"rls_practice_directory_read": directory})
    )


def test_a_change_to_the_planned_policies_does_not_match() -> None:
    """A registration added at runtime changes the plan, so the old state is stale."""
    table = "registry_probe_table"
    before = _plan(table, {"id", "user_id", "patient_id"})
    policies = {
        name: LivePolicy("*", True, "public", _DEPARSED, _DEPARSED, None)
        for name in before.policies
    }
    value = fingerprint(_SCHEMA, before, policies)
    live = LiveRls(
        {table: (True, True)},
        {table: {name: p._replace(comment=value) for name, p in policies.items()}},
    )
    assert table_matches(_SCHEMA, table, before, live)

    from app.db import PATIENT_READABLE_TABLES  # noqa: PLC0415

    register_overlay_patient_scoped(table)
    try:
        after = _plan(table, {"id", "user_id", "patient_id"})
        assert not table_matches(_SCHEMA, table, after, live)
    finally:
        PATIENT_READABLE_TABLES.pop(table, None)


def test_a_not_row_scoped_table_matches_only_with_rls_off() -> None:
    plan = TablePlan(
        ("ALTER TABLE practice_unit.users DISABLE ROW LEVEL SECURITY",),
        row_scoped=False,
        description="",
    )
    assert table_matches(_SCHEMA, "users", plan, LiveRls({"users": (False, False)}, {}))
    assert not table_matches(_SCHEMA, "users", plan, LiveRls({"users": (True, True)}, {}))


def test_fingerprints_do_not_depend_on_the_schema_name() -> None:
    """What lets a fingerprint captured in the template hold in every practice."""
    qualified = "practice_a.has_patient_access(patient_id, x)"
    assert (
        schema_neutral(qualified, "practice_a")
        == "__TENANT_SCHEMA__.has_patient_access(patient_id, x)"
    )
    assert schema_neutral("practice_ab.t", "practice_a") == "practice_ab.t"

    def _for(schema: str) -> str | None:
        plan = _plan_row_scoped_table(schema, "notes", {"id", "patient_id", "restricted"})
        policies = {
            name: LivePolicy("*", True, "public", f"{schema}.has_patient_access(x)", None, None)
            for name in plan.policies
        }
        return fingerprint(schema, plan, policies)

    first = _for("practice_a")
    assert first is not None
    assert first.startswith(FINGERPRINT_PREFIX)
    assert first == _for("practice_b")
