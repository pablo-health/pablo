# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""One row-scoped table's policies, chosen from its columns."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..rls_state import TablePlan
from .owner import OWNER_POLICY_NAME, _owner_policy_statement
from .patient import _patient_principal_policy_statements
from .purge import _patient_purge_policy_statements

if TYPE_CHECKING:
    from .registries import Registries

#: Tables whose rows belong to one note and are readable exactly when that
#: note is — its signed versions, its addenda and what was dictated for it.
#: ``infer_rls_on_schema`` gives them the ``rls_note_child_access`` policy,
#: keyed on ``note_id``.
NOTE_CHILD_TABLES: frozenset[str] = frozenset(
    {"note_signatures", "note_addenda", "session_dictations"}
)

#: Clinician-owned tables that any clinician with a live grant on the row's
#: client may also read. Writes stay with the row's ``user_id``.
GRANT_READABLE_TABLES: frozenset[str] = frozenset({"therapy_sessions", "appointments"})


def _plan_row_scoped_table(  # noqa: PLR0912, PLR0915 — one policy arm per tenant-table shape
    schema_name: str,
    table_name: str,
    columns: set[str],
    schema_tables: frozenset[str] = frozenset(),
    *,
    registries: Registries,
) -> TablePlan:
    """Every statement that sets one row-scoped table's RLS, in order, without running any.

    The full drop-and-recreate: enable and force RLS, drop every policy name
    this function has ever created, then create the ones the table's shape
    calls for. ``infer_rls_on_schema`` runs it only when the table does not
    already hold its result. Raises for a table that matches no policy shape.

    ``schema_tables`` is every table in the schema, for a policy that reads
    another table the schema may not have yet: older revisions run this
    mid-chain.
    """
    qualified = f"{schema_name}.{table_name}"
    statements: list[str] = []
    statements.append(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
    statements.append(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
    statements.append(f"DROP POLICY IF EXISTS rls_user_isolation ON {qualified}")
    statements.append(f"DROP POLICY IF EXISTS rls_patient_access ON {qualified}")
    statements.append(f"DROP POLICY IF EXISTS rls_patient_doc_access ON {qualified}")
    statements.append(f"DROP POLICY IF EXISTS rls_note_access ON {qualified}")
    statements.append(f"DROP POLICY IF EXISTS rls_note_child_access ON {qualified}")
    # Per-command policies on ``patients`` (split out from the
    # legacy single ALL policy to fix the INSERT chicken-and-egg).
    # Idempotent for tables that don't have these policies.
    statements.append(f"DROP POLICY IF EXISTS rls_patient_modify ON {qualified}")
    statements.append(f"DROP POLICY IF EXISTS rls_patient_delete ON {qualified}")
    statements.append(f"DROP POLICY IF EXISTS rls_patient_insert ON {qualified}")
    # The patient-principal arm. Dropped unconditionally so a table
    # that is later UNregistered sheds its patient policy on the next
    # run, rather than keeping a stale grant nobody is looking for.
    statements.append(f"DROP POLICY IF EXISTS rls_patient_self_read ON {qualified}")
    statements.append(f"DROP POLICY IF EXISTS rls_patient_self_write ON {qualified}")
    statements.append(f"DROP POLICY IF EXISTS rls_patient_self_insert ON {qualified}")
    statements.append(f"DROP POLICY IF EXISTS rls_patient_self_delete ON {qualified}")
    statements.append(f"DROP POLICY IF EXISTS rls_practice_answers ON {qualified}")
    statements.append(f"DROP POLICY IF EXISTS rls_patient_purge_read ON {qualified}")
    statements.append(f"DROP POLICY IF EXISTS rls_patient_purge_lock ON {qualified}")
    statements.append(f"DROP POLICY IF EXISTS rls_patient_purge_delete ON {qualified}")
    # The owner-scoped shape. Dropped from every table, like the patient arm
    # above, so a table whose registration is withdrawn sheds it. The drop
    # changes no table's fingerprint: that hashes the CREATE statements only.
    statements.append(f"DROP POLICY IF EXISTS {OWNER_POLICY_NAME} ON {qualified}")
    statements.append(f"DROP POLICY IF EXISTS rls_grant_read ON {qualified}")

    # Additive: created before the clinician shape is chosen, because
    # several of those branches return early. Permissive policies OR
    # together, so this widens access for a patient principal without
    # touching any clinician policy.
    statements.extend(
        _patient_principal_policy_statements(
            schema_name, table_name, columns, registries=registries
        )
    )
    # Additive in the same way, and only under the hard purge's own setting.
    statements.extend(_patient_purge_policy_statements(schema_name, table_name))

    # Pick the policy shape:
    #   * patient_documents — combined policy. Non-private rows
    #     follow patient_access (co-treaters see the chart);
    #     private rows collapse to uploader-only. Single CREATE
    #     POLICY with an OR so PG can short-circuit.
    #   * patients (the access target itself) — gate by id via the
    #     has_patient_access function.
    #   * Any other table with patient_id — gate by patient_id via
    #     has_patient_access.
    #   * Fallback to direct user_id ownership for tables that have
    #     a user_id column but no patient_id (e.g. availability_rules,
    #     google_calendar_tokens, ical_sync_configs).
    #   * A table registered owner-scoped comes first: the registration
    #     is the deployment saying which column owns the row, whatever
    #     else the table carries.
    owner_scope = registries.owner_scoped.get(table_name)
    if owner_scope is not None:
        if owner_scope.owner_column not in columns:
            raise RuntimeError(
                f"infer_rls_on_schema: {qualified} is registered owner-scoped on "
                f"'{owner_scope.owner_column}', but that column is not present "
                f"(columns found: {sorted(columns)}). Refusing to create a policy "
                f"that would match nothing — fix the registration or the table."
            )
        statements.append(_owner_policy_statement(qualified, owner_scope))
        shared = ", NULL shared" if owner_scope.shared_when_null else ""
        description = f"RLS (owner: {owner_scope.owner_column}{shared}) enabled on %s"
        return TablePlan(tuple(statements), row_scoped=True, description=description)
    if table_name == "patient_documents":
        # Non-restricted categories (chart, consent) → patient_access
        # (co-treaters share). category IN ('therapist_private',
        # 'psychotherapy_notes') → uploader-only. Both restricted
        # categories collapse to the same access predicate; the
        # distinction matters at the disclosure-workflow layer, not
        # RLS. Gated by the restricted set (not an enumerated
        # allow-list) so adding a future non-restricted category
        # needs no RLS change.
        statements.append(
            f"CREATE POLICY rls_patient_doc_access ON {qualified} "
            f"USING ("
            f"  (category NOT IN ('therapist_private', 'psychotherapy_notes') "
            f"   AND has_patient_access("
            f"    patient_id, current_setting('app.current_user_id', true)"
            f"  )) "
            f"  OR "
            f"  (category IN ('therapist_private', 'psychotherapy_notes') "
            f"   AND user_id::text = current_setting('app.current_user_id', true))"
            f")"
        )
        description = (
            "RLS (patient_doc_access: chart=patient_access, restricted=uploader) enabled on %s"
        )
        return TablePlan(tuple(statements), row_scoped=True, description=description)
    if table_name == "notes" and "restricted" in columns:
        # Same two-arm shape as patient_documents. An ordinary note
        # follows patient_access (co-treaters share the chart); a
        # restricted one (a psychotherapy note) is its author's alone.
        # Keyed on the ``restricted`` column rather than on
        # ``note_type`` so a future restricted type needs no RLS
        # change. The column guard is what lets the heal revision
        # earlier in the chain replay over a schema that predates it:
        # such a schema falls through to the plain patient_access
        # shape until its own upgrade adds the column.
        statements.append(
            f"CREATE POLICY rls_note_access ON {qualified} "
            f"USING ("
            f"  (NOT restricted "
            f"   AND has_patient_access("
            f"    patient_id, current_setting('app.current_user_id', true)"
            f"  )) "
            f"  OR "
            f"  (restricted "
            f"   AND author_user_id::text = current_setting('app.current_user_id', true))"
            f")"
        )
        description = "RLS (note_access: shared=patient_access, restricted=author) enabled on %s"
        return TablePlan(tuple(statements), row_scoped=True, description=description)
    if table_name in NOTE_CHILD_TABLES:
        # A note's signed versions and addenda carry its body and the
        # clinician's words about it, so they are readable exactly when
        # the note is. The subquery reads ``notes`` under its own row
        # policy, so a restricted note's children stay its author's and
        # an ordinary note's follow patient access, with no second copy
        # of either predicate to drift. ``qualified`` and ``schema_name``
        # are validated identifiers; nothing here is caller input.
        statements.append(
            f"CREATE POLICY rls_note_child_access ON {qualified} "  # noqa: S608
            f"USING (EXISTS ("
            f"  SELECT 1 FROM {schema_name}.notes n "
            f"  WHERE n.id = {qualified}.note_id"
            f"))"
        )
        description = "RLS (note_child_access: follows the parent note) enabled on %s"
        return TablePlan(tuple(statements), row_scoped=True, description=description)
    if table_name == "patient_source_mappings" and "scope" in columns:
        # A calendar's answers are shared by every armed clinician, a
        # feed's are their clinician's, and rows from before scopes carry
        # plain-text identifiers and stay their owner's until adopted. The
        # column guard lets the reconcile replay over a schema that
        # predates the ``scope`` column: such a schema keeps the
        # owner-only shape below until its own upgrade adds it.
        # ``single_practice_migration`` mirrors this branch.
        from ..practice_answers import practice_answers_policy_statements

        statements.extend(practice_answers_policy_statements(schema_name))
        description = "RLS (practice answers; old rows owner-only) enabled on %s"
        return TablePlan(tuple(statements), row_scoped=True, description=description)
    if table_name == "patient_clinicians":
        # The grant table itself: gating it via
        # ``has_patient_access(patient_id, …)`` would recurse
        # (the function reads patient_clinicians), and even a
        # non-recursive check on ``patient_id`` would create the
        # same INSERT chicken-and-egg as ``patients`` had — a
        # new grant's row can't satisfy "has a grant for this
        # patient" until that very grant exists. Gate by
        # ``user_id`` directly: each clinician sees only their
        # own grants, and INSERTs are permitted as long as the
        # row's ``user_id`` matches the current user (i.e. you
        # can't grant access to other clinicians from a normal
        # request path — admin endpoints would run with a
        # different effective user via SET ROLE or similar).
        statements.append(
            f"CREATE POLICY rls_user_isolation ON {qualified} "
            f"USING (user_id::text = current_setting('app.current_user_id', true)) "
            f"WITH CHECK (user_id::text = current_setting('app.current_user_id', true))"
        )
        description = "RLS (user_id) enabled on %s"
    elif table_name == "patients":
        # The ``patients`` table is the access-table target: a row's
        # grant in ``patient_clinicians`` is what makes the row
        # visible. Letting USING also gate INSERT (PG's default
        # when WITH CHECK is omitted) creates a chicken-and-egg —
        # ``has_patient_access(new_id, user)`` returns false for
        # a brand-new patient because the grant doesn't exist yet,
        # so the very first INSERT into a fresh tenant is rejected
        # ("new row violates row-level security policy"). This is
        # exactly the failure mode the 2026-05-17 pentest hit on
        # freshly-provisioned ``practice_pentest_*`` schemas.
        #
        # Split the policy: USING gates SELECT/UPDATE/DELETE
        # (only granted clinicians can read or modify rows), and
        # an explicit ``WITH CHECK (true)`` permits INSERTs. The
        # app inserts the patient + its primary-clinician grant
        # in the same flush so a new patient is immediately
        # visible to its creator. Other tables (sessions, notes,
        # etc.) keep the USING-as-WITH-CHECK shape — they SHOULD
        # require an existing grant to insert.
        statements.append(
            f"CREATE POLICY rls_patient_access ON {qualified} "
            f"FOR SELECT USING (has_patient_access("
            f"  id, current_setting('app.current_user_id', true)"
            f"))"
        )
        statements.append(
            f"CREATE POLICY rls_patient_modify ON {qualified} "
            f"FOR UPDATE USING (has_patient_access("
            f"  id, current_setting('app.current_user_id', true)"
            f"))"
        )
        statements.append(
            f"CREATE POLICY rls_patient_delete ON {qualified} "
            f"FOR DELETE USING (has_patient_access("
            f"  id, current_setting('app.current_user_id', true)"
            f"))"
        )
        # The clinician chicken-and-egg: a brand-new patient has no
        # ``patient_clinicians`` grant yet, so ``has_patient_access``
        # is false on the very INSERT that creates them. The policy
        # therefore cannot test patient access — but it must still
        # name a principal. Requiring an armed ``app.current_user_id``
        # admits any clinician (which is the point) while refusing a
        # patient principal, who arms ``app.current_patient_id`` and
        # leaves this one empty. ``WITH CHECK (true)`` admitted both.
        statements.append(
            f"CREATE POLICY rls_patient_insert ON {qualified} "
            f"FOR INSERT WITH CHECK ("
            f"coalesce(current_setting('app.current_user_id', true), '') <> ''"
            f")"
        )
        description = "RLS (patient_access split policies) enabled on %s"
    elif table_name == "audit_logs":
        # audit_logs carries user_id, so without this branch it would
        # take the generic clinician policy below — and that policy
        # compares against ``app.current_user_id``, which a patient
        # principal never arms. Every audit row a patient's own action
        # produced would be refused at INSERT, which under
        # § 164.312(b) is the one failure this table cannot have: the
        # action still happens, and nothing records it.
        #
        # So the policy splits by who acted. Anything that is not a
        # patient keeps exactly the predicate it has today, which is
        # what keeps clinician rows, anonymous public-booking rows and
        # system rows writing unchanged — they are all scoped to a
        # user_id and arm the user GUC. A patient actor is checked
        # against the patient GUC instead, and against their own id,
        # so one patient cannot write a row in another's name.
        insert_check = (
            "(actor_type IS DISTINCT FROM 'patient' AND "
            "user_id::text = current_setting('app.current_user_id', true)) OR "
            "(actor_type = 'patient' AND "
            "user_id::text = current_setting('app.current_patient_id', true))"
        )
        # Reads: an actor reads the rows they are the actor of. A
        # patient principal reads nothing at all.
        #
        # The one widening is the practice owner, who reads every row
        # about a client they have access to, whoever wrote it — the
        # review of activity on a client's record that the practice is
        # responsible for (§ 164.308(a)(1)(ii)(D)). That is its own
        # SELECT-only policy below, so it adds reads and nothing else.
        #
        # The audit log is still a compliance record, not a
        # read-surface for product behaviour. When a clinically
        # meaningful signal matters — that an intake packet was
        # completed, and when — it belongs in a domain column that
        # says so, queried like any other clinical fact. Deriving it
        # from audit rows would make the compliance record load-bearing
        # for product behaviour.
        select_using = "user_id::text = current_setting('app.current_user_id', true)"
        # The practice is found through this table's own schema, not a
        # schema name written into the policy: the tenant template is
        # captured from ``practice`` and rewrites only ``practice.``-
        # qualified names, which a regclass literal is and a string
        # literal is not. Same text as migration ``a7e3c5d19b42``;
        # ``test_audit_policy_parity`` holds the two together.
        #
        # No name it reaches may depend on the caller's search_path: it is
        # evaluated wherever an audit row is read, including the
        # RETURNING of an INSERT made from a session not scoped to this
        # tenant. ``has_patient_access`` is called schema-qualified, and
        # since ``a7e3c5d19b42`` its SQL-standard body is bound to its own
        # schema's ``patient_clinicians``, not found through the path.
        #
        # ``qualified`` is built from the validated schema name and a
        # fixed table name; not user input.
        owner_client_read = (
            "patient_id IS NOT NULL "  # noqa: S608
            f"AND {schema_name}.has_patient_access("
            "patient_id, current_setting('app.current_user_id', true)) "
            "AND EXISTS (SELECT 1 FROM platform.practices p "
            "WHERE p.owner_user_id::text = current_setting('app.current_user_id', true) "
            f"AND to_regclass(p.schema_name || '.audit_logs') = '{qualified}'::regclass)"
        )
        # The second widening: a clinician back from an away period reads
        # what the colleague who covered it did on their clients, and
        # nothing else of that colleague's. A row qualifies only if it was
        # written under a covering grant (``app.away.coverage`` tags it with
        # the period), the period is the reader's own, the row's actor is
        # that period's covering colleague, and the reader still has
        # access to the client. SELECT-only, like the owner's. Same text as
        # migration ``e7b3d9a2c4f1``; ``test_audit_policy_parity`` holds the
        # two together. Created only once the schema has the away-periods
        # table: older revisions run this function mid-chain, before it.
        coverage_read = (
            "patient_id IS NOT NULL "  # noqa: S608
            "AND (changes ->> 'coverage_period_id') IS NOT NULL "
            f"AND EXISTS (SELECT 1 FROM {schema_name}.clinician_away_periods ap "
            "WHERE ap.id::text = (audit_logs.changes ->> 'coverage_period_id') "
            "AND ap.user_id::text = current_setting('app.current_user_id', true) "
            "AND ap.covering_user_id::text = audit_logs.user_id) "
            f"AND {schema_name}.has_patient_access("
            "patient_id, current_setting('app.current_user_id', true))"
        )
        # One ALL policy, the same shape the generic branch uses, so
        # UPDATE and DELETE keep a predicate. Splitting this into
        # SELECT-only and INSERT-only policies would leave those two
        # commands with no policy at all — under RLS that is not a
        # refusal, it is a silent match against zero rows, which would
        # turn a tampering attempt from a loud trigger error into a
        # quiet no-op and would strand the retention purge.
        # The retention purge is not a principal and has no identity to
        # arm: ``_delete_expired`` sets ``search_path``, arms
        # ``app.allow_audit_purge`` and issues one DELETE across every
        # actor's rows. Under the actor policy alone that DELETE compares
        # ``user_id`` against an unset GUC and matches nothing — silently,
        # rowcount 0, a cron reporting a successful run having purged
        # nothing while rows outlive ``expires_at`` forever.
        #
        # So the purge gets its own permissive policies, gated on the same
        # GUC the append-only trigger already treats as the authorization
        # to delete. No new trust boundary: anything that can set that GUC
        # can already get a DELETE past the trigger, and RLS was only
        # hiding the rows by accident.
        #
        # SELECT as well as DELETE, because ``_count_expired`` backs
        # ``--dry-run`` and reads through the same predicate. Without it an
        # operator asking whether the purge has work is told "none" while
        # the real run deletes thousands — a worse failure than both
        # reporting zero. Both are gated on the purge GUC, so an ordinary
        # request (which never sets it) sees exactly what it saw before.
        #
        # Deliberately NOT ``FOR ALL``: that would also admit INSERT and
        # UPDATE under the purge GUC, letting a purge-context session
        # forge or rewrite rows. The trigger blocks UPDATE, but the policy
        # should not be the thing relying on it.
        purge_armed = "current_setting('app.allow_audit_purge', true) = 'on'"
        statements.append(f"DROP POLICY IF EXISTS rls_user_isolation ON {qualified}")
        statements.append(f"DROP POLICY IF EXISTS rls_audit_actor_access ON {qualified}")
        statements.append(f"DROP POLICY IF EXISTS rls_audit_purge_delete ON {qualified}")
        statements.append(f"DROP POLICY IF EXISTS rls_audit_purge_select ON {qualified}")
        statements.append(f"DROP POLICY IF EXISTS rls_audit_owner_client_read ON {qualified}")
        statements.append(f"DROP POLICY IF EXISTS rls_audit_coverage_read ON {qualified}")
        statements.append(
            f"CREATE POLICY rls_audit_actor_access ON {qualified} "
            f"USING ({select_using}) WITH CHECK ({insert_check})"
        )
        statements.append(
            f"CREATE POLICY rls_audit_owner_client_read ON {qualified} "
            f"FOR SELECT USING ({owner_client_read})"
        )
        if "clinician_away_periods" in schema_tables:
            statements.append(
                f"CREATE POLICY rls_audit_coverage_read ON {qualified} "
                f"FOR SELECT USING ({coverage_read})"
            )
        statements.append(
            f"CREATE POLICY rls_audit_purge_delete ON {qualified} FOR DELETE USING ({purge_armed})"
        )
        statements.append(
            f"CREATE POLICY rls_audit_purge_select ON {qualified} FOR SELECT USING ({purge_armed})"
        )
        description = "RLS (audit actor access + retention purge) enabled on %s"
    elif "user_id" in columns:
        # Tables where a row has a direct owning clinician
        # (therapy_sessions, appointments, audit_logs, etc.).
        # Each user sees their own rows (and grant reads). WITH CHECK matches
        # USING explicitly so INSERTs are permitted as long as the
        # new row's ``user_id`` matches the current user — no
        # chicken-and-egg, and you can't insert a row claiming
        # someone else's user_id from a normal request path.
        #
        # ``patient_id`` is checked AFTER ``user_id`` (despite
        # both columns sometimes coexisting on the same table —
        # e.g. ``therapy_sessions``, ``audit_logs``) because the
        # documented intent is "user owns the row directly", and
        # the patient_id-based policy is only meant for tables
        # that have *no* user_id column (currently just
        # ``notes``).
        statements.append(
            f"CREATE POLICY rls_user_isolation ON {qualified} "
            f"USING (user_id::text = current_setting('app.current_user_id', true)) "
            f"WITH CHECK (user_id::text = current_setting('app.current_user_id', true))"
        )
        description = "RLS (user_id) enabled on %s"
        if table_name in GRANT_READABLE_TABLES and "patient_id" in columns:
            # Permissive policies OR together per command, so this widens
            # SELECT only; UPDATE, DELETE and INSERT still need user_id.
            statements.append(
                f"CREATE POLICY rls_grant_read ON {qualified} "
                f"FOR SELECT USING (has_patient_access("
                f"  patient_id, current_setting('app.current_user_id', true)"
                f"))"
            )
            description = "RLS (user_id; reads also by patient grant) enabled on %s"
    elif "patient_id" in columns:
        statements.append(
            f"CREATE POLICY rls_patient_access ON {qualified} "
            f"USING (has_patient_access("
            f"  patient_id, "
            f"  current_setting('app.current_user_id', true)"
            f"))"
        )
        description = "RLS (patient_access) enabled on %s"
    elif table_name == "chat_messages":
        # chat_messages has neither user_id nor patient_id — gate
        # by the parent conversation's patient. Without this branch
        # the loop ENABLE+FORCEs RLS but leaves no policy, which is
        # a deny-all configuration that only the BYPASSRLS-on-role
        # posture mistakenly hides in production.
        statements.append(f"DROP POLICY IF EXISTS rls_chat_message_access ON {qualified}")
        # ``qualified`` + ``schema_name`` are validated identifiers
        # built from the validated schema name (see _validate_schema_name
        # earlier) and a fixed table name; not user input.
        statements.append(
            f"CREATE POLICY rls_chat_message_access ON {qualified} "  # noqa: S608
            f"USING (EXISTS ("
            f"  SELECT 1 FROM {schema_name}.chat_conversations c "
            f"  WHERE c.id = {qualified}.conversation_id "
            f"    AND has_patient_access("
            f"      c.patient_id, "
            f"      current_setting('app.current_user_id', true)"
            f"    )"
            f"))"
        )
        description = "RLS (chat_message_access via parent conversation) enabled on %s"
    elif table_name == "compliance_documents":
        # compliance_documents owns rows directly via
        # ``uploaded_by_user_id`` — but the column isn't literally
        # named ``user_id``, so the user_id branch above misses it
        # and the table would otherwise fall through to a deny-all.
        # Gate by the uploader rather than the parent
        # ``compliance_item_id`` (which is nullable, so a parent-based
        # policy would hide item-less documents); uploaded_by_user_id
        # is NOT NULL, so every row is attributable.
        owner = "uploaded_by_user_id::text = current_setting('app.current_user_id', true)"
        statements.append(
            f"CREATE POLICY rls_user_isolation ON {qualified} USING ({owner}) WITH CHECK ({owner})"
        )
        description = "RLS (uploaded_by_user_id) enabled on %s"
    else:
        # We force-enabled RLS at the top of the loop but matched no
        # policy shape. Leaving it here would be a silent deny-all
        # (the exact trap the chat_messages branch calls out). Fail
        # loud so a newly-added tenant table can't ship deny-all:
        # the author must add a policy branch above, or list the
        # table in ``not_row_scoped`` if it isn't row-owned.
        raise RuntimeError(
            f"infer_rls_on_schema: no RLS policy defined for {qualified} "
            f"(columns present: {sorted(columns)}). Add a policy branch "
            f"or list the table in ``not_row_scoped`` — refusing to leave "
            f"it deny-all."
        )
    return TablePlan(tuple(statements), row_scoped=True, description=description)
