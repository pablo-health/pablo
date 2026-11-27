# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The platform capture leaves out a policy naming a role, and keeps the rest."""

from __future__ import annotations

from scripts.regen_platform_schema import _post_process

# Captured from pg_dump 16 on a database at the platform chain's head.
_ROLE_POLICY = (
    "CREATE POLICY rls_practice_directory_read ON platform.payer_participations FOR SELECT "
    "TO pablo_practice_directory USING (((COALESCE(current_setting('app.current_user_id'::text, "
    "true), ''::text) <> ''::text) AND ((practice_id)::text = ANY (current_schemas(false)))));"
)
_PUBLIC_POLICY = (
    "CREATE POLICY rls_credential_owner ON platform.payer_participations USING "
    "(((user_id)::text = current_setting('app.current_user_id'::text, true))) WITH CHECK "
    "(((user_id)::text = current_setting('app.current_user_id'::text, true)));"
)


def test_a_policy_naming_a_role_is_left_to_its_revision() -> None:
    template = _post_process(f"{_ROLE_POLICY}\n\n{_PUBLIC_POLICY}\n")
    assert "rls_practice_directory_read" not in template
    assert _PUBLIC_POLICY in template
