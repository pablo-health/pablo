# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The hard purge's policies, gated on ``app.allow_patient_purge``."""

from __future__ import annotations

#: What the hard purge (``app.jobs.hard_purge_cron``) does to each table it
#: touches, beyond reading it. ``patients`` also needs ``lock``: the job takes
#: the row ``FOR UPDATE`` so a restore cannot race the purge, and Postgres
#: tests that against the UPDATE policies. ``patient_documents`` and
#: ``session_dictations`` are read for their storage paths only; their rows go
#: with the patient by foreign-key cascade, which row security does not apply to.
#: ``tasks`` is deleted by the job except for compliance tasks; the job's
#: ``_PATIENT_ROW_TABLES`` says why the tables it deletes by name are. The
#: intake, charge, payment-method and calendar tables keep their ``delete``
#: here although they now go by foreign-key cascade (``8c5e65a0e56a``):
#: narrowing a table's policies is a reconcile change of its own.
PATIENT_PURGE_TABLES: dict[str, frozenset[str]] = {
    "patients": frozenset({"read", "lock", "delete"}),
    "appointments": frozenset({"read", "delete"}),
    "notes": frozenset({"read", "delete"}),
    "therapy_sessions": frozenset({"read", "delete"}),
    "patient_source_mappings": frozenset({"read", "delete"}),
    "chat_conversations": frozenset({"read", "delete"}),
    "chat_messages": frozenset({"read", "delete"}),
    "patient_documents": frozenset({"read"}),
    "session_dictations": frozenset({"read"}),
    "patient_message_threads": frozenset({"read", "delete"}),
    "patient_intake_assignments": frozenset({"read", "delete"}),
    "patient_intake_submissions": frozenset({"read", "delete"}),
    "patient_charges": frozenset({"read", "delete"}),
    "patient_payment_methods": frozenset({"read", "delete"}),
    "external_calendar_events": frozenset({"read", "delete"}),
    "tasks": frozenset({"read", "delete"}),
}

#: ``coalesce`` because a connection that never set the setting reads it as
#: NULL, not ``''``, and ``NULL = 'on'`` is NULL. ``NULL AND EXISTS (...)``
#: cannot short-circuit (it is false if the EXISTS is false), so the purge
#: arm ran its ``patients`` subquery, row security and all, for every row of
#: every ordinary read of these tables: about half the cost of counting a
#: clinician's open notes. A NULL policy result already grants nothing, so
#: who can read what is unchanged; the subquery just stops running.
_PATIENT_PURGE_ARMED = "coalesce(current_setting('app.allow_patient_purge', true), '') = 'on'"


def _patient_purge_policy_statements(schema_name: str, table_name: str) -> list[str]:
    """The hard purge's policies on one table, gated on ``app.allow_patient_purge``.

    The purge is not a principal: it has no clinician to arm, and the app
    role does not bypass row-level security, so without these it reads no
    patients and deletes nothing — and exits 0 as if it had. Same shape as
    the audit retention purge's policies on ``audit_logs``, and the same
    trust boundary: the only code in ``app`` that sets the setting is the
    job, transaction-locally (``SET LOCAL``), so it never reaches a pooled
    connection or a request. Anything that could set it some other way is
    already running SQL as the schema owner, which can switch row security
    off outright.

    Narrow even when armed. ``patients`` admits only soft-deleted rows, and
    every other table only rows whose patient is soft-deleted, so the setting
    can never expose a current client. No INSERT arm, and the UPDATE arm on
    ``patients`` exists only for the row lock: its ``WITH CHECK (false)``
    refuses any actual update.

    ``schema_name`` is validated and the table names are fixed; no caller
    input reaches these strings.
    """
    commands = PATIENT_PURGE_TABLES.get(table_name)
    if commands is None:
        return []
    qualified = f"{schema_name}.{table_name}"
    patients = f"{schema_name}.patients"
    if table_name == "patients":
        predicate = f"{_PATIENT_PURGE_ARMED} AND deleted_at IS NOT NULL"
    elif table_name == "chat_messages":
        predicate = (
            f"{_PATIENT_PURGE_ARMED} AND EXISTS ("  # noqa: S608
            f"SELECT 1 FROM {schema_name}.chat_conversations c "
            f"JOIN {patients} p ON p.id = c.patient_id "
            f"WHERE c.id = {qualified}.conversation_id AND p.deleted_at IS NOT NULL)"
        )
    else:
        predicate = (
            f"{_PATIENT_PURGE_ARMED} AND EXISTS ("  # noqa: S608
            f"SELECT 1 FROM {patients} p "
            f"WHERE p.id = {qualified}.patient_id AND p.deleted_at IS NOT NULL)"
        )
    statements = [
        f"CREATE POLICY rls_patient_purge_read ON {qualified} FOR SELECT USING ({predicate})"
    ]
    if "lock" in commands:
        statements.append(
            f"CREATE POLICY rls_patient_purge_lock ON {qualified} "
            f"FOR UPDATE USING ({predicate}) WITH CHECK (false)"
        )
    if "delete" in commands:
        statements.append(
            f"CREATE POLICY rls_patient_purge_delete ON {qualified} FOR DELETE USING ({predicate})"
        )
    return statements
