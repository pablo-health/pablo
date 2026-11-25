# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The patient principal's policies: which tables a client reaches, and how."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registries import Registries

# Of the writable ones, the ones a patient may also DELETE. A third registry
# rather than a widening of "writable", because the two chat tables are the
# only case where the patient owns the row outright and purging it is the
# feature: a patient who deletes a conversation expects it gone, not
# archived. ``outcome_measures`` stays out — a screener result is part of
# the clinical record once submitted, and the write arm exists so the form
# can save it, not so it can be withdrawn.
#
# ``patient_intake_artifacts`` joins them for a narrower reason. A patient
# who photographs the back of their card badly has to be able to take it
# again, and "take it again" means the first one goes: an artifact left
# behind would hold the one slot the card's back has. The document it
# pointed at is tombstoned in the same breath by the route. Only before the
# form is handed in — afterwards the route refuses, because what was
# submitted has to stay what was submitted.
PATIENT_DELETABLE_TABLES: frozenset[str] = frozenset(
    {"chat_conversations", "chat_messages", "patient_intake_artifacts"}
)

# An extra condition ANDed onto the patient's WRITE arms only, for the
# tables where "may read" and "may write" are not the same set of rows.
#
# Row-level security has no column granularity, so a write grant is
# ordinarily as wide as the row — which is why every other narrowing in
# this feature is the route layer's. This registry exists for the one case
# the route layer cannot make safe on its own: a table whose rows are
# mostly the practice's acts, carrying one kind the patient causes. A
# patient may write that kind and no other, and saying so in the policy
# means a route that forgot cannot mint an acceptance under a patient
# principal.
#
# Read stays wide on purpose: the patient is shown the correction the
# practice asked for, which is a row they may not write.
PATIENT_WRITE_NARROWING: dict[str, str] = {
    "patient_intake_review_events": "kind = 'corrected'",
}


def _patient_principal_predicate(key_column: str) -> str:
    """The row test for "this row belongs to the calling patient".

    Same ``::text``-cast idiom as the clinician arm: the column is a native
    ``uuid`` and the GUC is always text, so casting the column (rather than
    the GUC) means an unset GUC yields NULL and matches nothing — fail
    closed, with no ``invalid input syntax for uuid`` path an attacker
    could use to distinguish states.
    """
    return f"{key_column}::text = current_setting('app.current_patient_id', true)"


def _patient_principal_predicate_for(
    table_name: str, key_column: str, schema_name: str, qualified: str
) -> str:
    """The patient row test for one table, bespoke where the shape demands it.

    Most tables are owned by a patient through a plain column and take
    :func:`_patient_principal_predicate` unchanged. Three tables are not,
    and each is a different kind of not.

    ``patient_documents`` carries the right column, and the rows it matches
    are genuinely the patient's own — they are on that patient's chart. But
    the chart is not the portal. The same table holds the clinician's working
    material and the ``psychotherapy_notes`` carve-out that patient
    right-of-access explicitly does not reach (§164.524(a)(1)(i)), so the
    plain predicate would make the strictest category in the system readable
    by the person it is withheld from, the moment a route forgot its filter.
    The category test joins the predicate here so that forgetting is not
    sufficient. The routes filter too, and the repository filters again:
    none of the three is the reason to skip the others.

    ``chat_conversations`` carries the right column but the wrong meaning.
    Its ``patient_id`` names the conversation's *subject*, and a clinician's
    chat about a patient is subject to that same patient — so the plain
    predicate would let a patient read the clinician's conversations about
    them, which is the opposite of what a patient-facing surface should
    disclose. ``owner_user_id IS NULL`` is what distinguishes the rows the
    patient started (the column is nullable precisely to carry that
    distinction), so it joins the predicate here.

    The route layer tests the same thing independently, and deliberately:
    a policy is a backstop for a route that forgot, and a route check is a
    backstop for a schema that was migrated without its policies. Neither
    is the reason to skip the other.

    ``chat_messages`` has no owning column at all — not ``patient_id``, not
    ``user_id`` — so it is scoped through the parent conversation, and
    inherits both halves of the test above by construction.

    ``homework_assignments`` carries the right column, but a row the clinician
    withdrew is no longer the client's. The status test keeps it off the
    portal even if a route forgot.
    """
    if table_name == "patient_documents":
        from ...models import PATIENT_FACING_CATEGORIES

        # Rendered from the enum rather than spelled out, so a category
        # added to the patient's surface cannot reach the routes while the
        # policy still refuses it.
        allowed = ", ".join(f"'{c.value}'" for c in sorted(PATIENT_FACING_CATEGORIES))
        return f"{_patient_principal_predicate(key_column)} AND {qualified}.category IN ({allowed})"
    if table_name == "chat_conversations":
        return f"{_patient_principal_predicate(key_column)} AND {qualified}.owner_user_id IS NULL"
    if table_name == "homework_assignments":
        # A withdrawn assignment is no longer the client's.
        return (
            f"{_patient_principal_predicate(key_column)} "
            f"AND {qualified}.status IN ('assigned', 'done')"
        )
    if table_name == "chat_messages":
        # ``schema_name`` and ``qualified`` are validated identifiers (see
        # ``_validate_schema_name``) composed with fixed table names; the
        # only interpolation is ours, never a caller's.
        parent = f"{schema_name}.chat_conversations"
        owns = "c.patient_id::text = current_setting('app.current_patient_id', true)"
        # Every identifier below is validated (see _validate_schema_name) or
        # a literal table name; no caller input reaches the string.
        return (
            f"EXISTS (SELECT 1 FROM {parent} c "  # noqa: S608
            f"WHERE c.id = {qualified}.{key_column} "
            f"AND {owns} AND c.owner_user_id IS NULL)"
        )
    return _patient_principal_predicate(key_column)


def _patient_principal_policy_statements(
    schema_name: str, table_name: str, columns: set[str], *, registries: Registries
) -> list[str]:
    """The additive patient-principal policies for a registered table.

    Planned for every table before the clinician policy shape is chosen, so
    a registered table gets its patient arm regardless of which clinician
    branch handles it.

    Unregistered tables get nothing — which is the point. A clinician-
    scoped table keyed on ``app.current_user_id`` fails closed for a
    patient principal automatically, because a patient request never arms
    that GUC. That is asserted directly in the integration suite rather
    than assumed.

    ``columns`` must be the table's FULL column set, not the scoping-column
    subset the policy-shape branches switch on. Against the subset this
    check could only ever accept ``patient_id`` or ``id`` — the two key
    columns core happens to use — and would reject every other one, so the
    first table registered on, say, ``submitted_by_patient_id`` would fail
    tenant provisioning over a column that is right there in the table.
    Worse, the unit-level registry test would stay green throughout,
    because it checks the ORM metadata, which has all the columns.
    """
    qualified = f"{schema_name}.{table_name}"
    key_column = registries.patient_readable.get(table_name)
    if key_column is None:
        return []

    if key_column not in columns:
        raise RuntimeError(
            f"enable_rls_on_schema: {qualified} is registered patient-scoped on "
            f"'{key_column}', but that column is not present (columns found: "
            f"{sorted(columns)}). Refusing to create a policy that would match "
            f"nothing — fix the registration or the table."
        )

    predicate = _patient_principal_predicate_for(table_name, key_column, schema_name, qualified)
    statements = [
        f"CREATE POLICY rls_patient_self_read ON {qualified} FOR SELECT USING ({predicate})"
    ]

    if table_name in registries.patient_writable:
        # The write arm, which is the read predicate unless the table asked
        # for less (see ``PATIENT_WRITE_NARROWING``). The extra condition is
        # a fixed string from this module, never a caller's.
        narrowing = PATIENT_WRITE_NARROWING.get(table_name)
        writable = predicate if narrowing is None else f"({predicate}) AND ({narrowing})"
        statements.append(
            f"CREATE POLICY rls_patient_self_write ON {qualified} "
            f"FOR UPDATE USING ({writable}) WITH CHECK ({writable})"
        )
        # INSERT needs a policy of its own: an UPDATE policy does not cover
        # it, and under FORCE ROW LEVEL SECURITY an INSERT with no matching
        # policy is refused outright — which is why a table registered
        # ``writable`` was, until this arm existed, still unwritable by the
        # patient the registration names. ``WITH CHECK`` only, since there
        # is no prior row for ``USING`` to test: the predicate pins the NEW
        # row's owning column to the calling patient, and that is what stops
        # A from writing a row owned by B.
        statements.append(
            f"CREATE POLICY rls_patient_self_insert ON {qualified} "
            f"FOR INSERT WITH CHECK ({writable})"
        )

    if table_name in PATIENT_DELETABLE_TABLES:
        # DELETE is the quiet one. With no DELETE policy the row is simply
        # not visible to the command, so a purge deletes zero rows without
        # an error and the caller is told it succeeded. The arm makes the
        # patient's own rows deletable and nothing else: the predicate is
        # the same ownership pair the read arm uses.
        statements.append(
            f"CREATE POLICY rls_patient_self_delete ON {qualified} FOR DELETE USING ({predicate})"
        )
    return statements
