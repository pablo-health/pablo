# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Row-level security inferred from each table's columns.

The planner that decides a tenant table's policies from its shape: which of
``id`` / ``user_id`` / ``patient_id`` / ``note_id`` it carries, plus what a
deployment registered. ``app.db.enable_rls_on_schema`` calls it, and the regen
captures what it produces into ``tenant_template.sql``.

Every function here takes the registrations it needs as arguments
(``Registries``); none reads module-global state.
"""
