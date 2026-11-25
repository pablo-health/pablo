# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Policy rendering and stored-form fingerprints."""

import pytest
from app.db.rls_apply import APPLIED_PREFIX, applied_fingerprint, render_policy
from app.db.rls_state import LivePolicy


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (
            LivePolicy("*", True, "public", "(practice_x.f(id))", "(practice_x.f(id))", None),
            "CREATE POLICY p ON __TENANT_SCHEMA__.t "
            "USING ((__TENANT_SCHEMA__.f(id))) WITH CHECK ((__TENANT_SCHEMA__.f(id)));",
        ),
        (
            LivePolicy("r", True, "public", "true", None, None),
            "CREATE POLICY p ON __TENANT_SCHEMA__.t FOR SELECT USING (true);",
        ),
        (
            LivePolicy("a", True, "public", None, "false", None),
            "CREATE POLICY p ON __TENANT_SCHEMA__.t FOR INSERT WITH CHECK (false);",
        ),
        (
            LivePolicy("d", False, "app,reader", "true", None, None),
            "CREATE POLICY p ON __TENANT_SCHEMA__.t AS RESTRICTIVE FOR DELETE "
            "TO app, reader USING (true);",
        ),
    ],
)
def test_a_live_policy_renders_as_pg_dump_writes_it(policy: LivePolicy, expected: str) -> None:
    assert render_policy("practice_x", "t", "p", policy) == expected


def test_the_applied_fingerprint_changes_with_either_half() -> None:
    """It ties one statement to one stored form, so neither can change under it."""
    base = applied_fingerprint("CREATE POLICY p ON t USING (a);", "CREATE POLICY p ON t USING (b);")
    assert base.startswith(APPLIED_PREFIX)
    assert base == applied_fingerprint(
        "CREATE POLICY p ON t USING (a);", "CREATE POLICY p ON t USING (b);"
    )
    assert base != applied_fingerprint(
        "CREATE POLICY p ON t USING (a);", "CREATE POLICY p ON t USING (true);"
    )
    assert base != applied_fingerprint(
        "CREATE POLICY p ON t USING (c);", "CREATE POLICY p ON t USING (b);"
    )
