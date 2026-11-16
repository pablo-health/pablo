# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Synthesize one owned row per row-scoped table, through RLS, as the app role.

``test_rls_proof.py`` needs a row in every row-scoped table, of a practice
schema and of ``platform``, that belongs to one principal, so it can then
try to reach that row as every other principal. Writing a fixture per
table would go stale the day a table is added, which is the drift the proof
exists to catch. So the rows are made from the catalog instead:

* each column is filled from its type, its CHECK constraint (the first value
  an ``IN (...)`` / ``= ANY (ARRAY[...])`` list allows) or its enum;
* a foreign key is satisfied by synthesizing the parent first, recursively,
  so no constraint is switched off and no superuser is involved;
* the columns policies key on (``user_id``, ``patient_id``, ``note_id``, the
  ``*_user_id`` owners) are pointed at the owning principal.

Every insert runs as the ordinary app role with the owner armed through the
app's own ``arm_current_user_id``, so each row has passed its table's
``WITH CHECK`` exactly as an app write would. A table this cannot fill is
reported by name with the database's error, never skipped.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Columns a policy keys on, pointed at the owner rather than synthesized.
_USER_KEY_RE = re.compile(r"^(user_id|.*_user_id|attested_by|created_by)$")
_ANY_ARRAY_RE = re.compile(r"\(\(?(\w+)\)?(?:::text)?\s*=\s*ANY\s*\(\(?ARRAY\[(.*?)\]")
_IN_LITERAL_RE = re.compile(r"'((?:[^']|'')*)'")
_EQ_LITERAL_RE = re.compile(r"\(\(?(\w+)\)?(?:::text)?\s*=\s*'((?:[^']|'')*)'")


@dataclass
class Column:
    name: str
    data_type: str
    udt_name: str
    nullable: bool
    has_default: bool
    is_identity: bool
    max_length: int | None = None


@dataclass
class Table:
    name: str
    columns: list[Column]
    pk: list[str]
    # column -> (parent table, parent column); a composite key maps each of
    # its columns, all to the same parent row
    fks: dict[str, tuple[str, str]] = field(default_factory=dict)
    # column -> allowed literal values from CHECK constraints
    allowed: dict[str, list[str]] = field(default_factory=dict)


#: Stands for the owning clinician's id in :data:`CHECK_OVERRIDES`.
OWNER_USER = object()

#: Values for CHECK constraints that tie two columns together, which reading
#: one column's allowed list cannot satisfy. Keyed by table, then column; each
#: entry names the constraint it answers. A new constraint of this kind fails
#: synthesis by name, and the fix is one line here.
CHECK_OVERRIDES: dict[str, dict[str, object]] = {
    # ck_client_ai_consent_events_recorded_by: a clinician-sourced event names who recorded it
    "client_ai_consent_events": {"recorded_by": OWNER_USER},
    # ck_contracted_rates_basis_fields (platform): a fixed rate carries an amount
    "contracted_rates": {"basis": "fixed", "amount_cents": 100},
    # ck_credential_confirmations_corrected (platform): a correction or a confirmation
    "credential_confirmations": {"confirmed": True},
    # ck_llm_usage_period_yyyymm_shape
    "llm_usage": {"period_yyyymm": "202611"},
    # ck_patient_charges_method_kind: a session charge carries a method
    "patient_charges": {"kind": "session", "method": "card"},
    # ck_patient_preferences_has_content
    "patient_preferences": {"text": "rls proof"},
    # ck_remittance_holds_line_reason: a line_balance hold names its line
    "remittance_holds": {"reason": "line_balance", "line_control_number": "1"},
}


@dataclass
class Owner:
    """The principal a synthesized row belongs to."""

    user_id: str
    patient_id: str


def load_catalog(session: Session, schema: str) -> dict[str, Table]:
    """Read every base table of ``schema`` with its columns, keys and checks."""
    tables: dict[str, Table] = {}
    cols = session.execute(
        text(
            "SELECT table_name, column_name, data_type, udt_name, is_nullable = 'YES', "
            "column_default IS NOT NULL, is_identity = 'YES', character_maximum_length "
            "FROM information_schema.columns WHERE table_schema = :s "
            "ORDER BY table_name, ordinal_position"
        ),
        {"s": schema},
    ).all()
    for t, name, dtype, udt, nullable, has_default, ident, max_len in cols:
        tables.setdefault(t, Table(name=t, columns=[], pk=[]))
        tables[t].columns.append(Column(name, dtype, udt, nullable, has_default, ident, max_len))
    cons = session.execute(
        text(
            "SELECT c.relname, con.contype, pg_get_constraintdef(con.oid), "
            "array(SELECT a.attname FROM unnest(con.conkey) k "
            "      JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k), "
            "fc.relname, "
            "array(SELECT a.attname FROM unnest(con.confkey) k "
            "      JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k), "
            "fn.nspname "
            "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_class fc ON fc.oid = con.confrelid "
            "LEFT JOIN pg_namespace fn ON fn.oid = fc.relnamespace "
            "WHERE n.nspname = :s"
        ),
        {"s": schema},
    ).all()
    for rel, kind, definition, keys, parent, parent_keys, parent_ns in cons:
        table = tables.get(rel)
        if table is None:
            continue
        if kind == "p":
            table.pk = list(keys)
        elif kind == "f" and parent_ns == schema:
            for key, parent_key in zip(keys, parent_keys, strict=True):
                table.fks.setdefault(key, (parent, parent_key))
        elif kind == "c":
            for col, body in _ANY_ARRAY_RE.findall(definition):
                table.allowed.setdefault(
                    col, [v.replace("''", "'") for v in _IN_LITERAL_RE.findall(body)]
                )
            for col, value in _EQ_LITERAL_RE.findall(definition):
                table.allowed.setdefault(col, [value.replace("''", "'")])
    return tables


def _enum_first(session: Session, udt: str) -> str | None:
    row = session.execute(
        text(
            "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = :t ORDER BY e.enumsortorder LIMIT 1"
        ),
        {"t": udt},
    ).first()
    return row[0] if row else None


def _value_for(session: Session, table: Table, col: Column) -> tuple[str, Any]:  # noqa: PLR0911, PLR0912
    """A SQL expression and bind value for one synthesized column."""
    allowed = table.allowed.get(col.name)
    if allowed:
        return ":v", allowed[0]
    dt, udt = col.data_type, col.udt_name
    if dt == "USER-DEFINED":
        label = _enum_first(session, udt)
        if label is not None:
            return ":v", label
    if dt == "uuid":
        return ":v", str(uuid.uuid4())
    if dt in ("character varying", "text", "character"):
        token = f"rls-{uuid.uuid4().hex[:12]}"
        return ":v", token[: col.max_length] if col.max_length else token
    if dt in ("integer", "bigint", "smallint", "numeric", "double precision", "real"):
        return ":v", 1
    if dt == "boolean":
        return ":v", False
    if dt.startswith("timestamp"):
        return "now()", None
    if dt == "date":
        return "current_date", None
    if dt.startswith("time"):
        return "'09:00'::time", None
    if dt == "interval":
        return "interval '1 hour'", None
    if dt in ("json", "jsonb"):
        return f"'{{}}'::{dt}", None
    if dt == "ARRAY":
        return f"'{{}}'::{udt[1:]}[]", None
    if dt == "bytea":
        return "'\\x00'::bytea", None
    if dt == "inet":
        return "'127.0.0.1'::inet", None
    if dt == "tstzrange":
        return "tstzrange(now(), now() + interval '1 hour')", None
    return ":v", "x"


class Synthesizer:
    """Fill tables of one schema with rows owned by one principal."""

    def __init__(self, session: Session, schema: str, owner: Owner, catalog: dict[str, Table]):
        self.session = session
        self.schema = schema
        self.owner = owner
        self.catalog = catalog
        # table -> {column: value} of the row synthesized for it (pk included)
        self.rows: dict[str, dict[str, Any]] = {}

    def row_for(self, table_name: str, _depth: int = 0) -> dict[str, Any]:  # noqa: PLR0912
        """Synthesize (once) and return the owned row of ``table_name``."""
        if table_name in self.rows:
            return self.rows[table_name]
        if _depth > 8:
            msg = f"foreign-key chain too deep at {table_name}"
            raise RuntimeError(msg)
        table = self.catalog[table_name]
        exprs: list[str] = []
        names: list[str] = []
        params: dict[str, Any] = {}
        for i, col in enumerate(table.columns):
            if col.is_identity:
                continue
            value_sql: str | None = None
            value: Any = None
            fk = table.fks.get(col.name)
            override = CHECK_OVERRIDES.get(table_name, {})
            if col.name in override:
                chosen = override[col.name]
                value_sql, value = ":v", self.owner.user_id if chosen is OWNER_USER else chosen
            elif col.name == "patient_id" and table_name != "patients":
                if fk is not None:
                    self.row_for(fk[0], _depth + 1)
                value_sql, value = ":v", self.owner.patient_id
            elif fk is not None and (
                not col.nullable or col.name in ("note_id", "conversation_id")
            ):
                parent = self.row_for(fk[0], _depth + 1)
                value_sql, value = ":v", parent[fk[1]]
            elif _USER_KEY_RE.match(col.name) and col.data_type in (
                "uuid",
                "character varying",
                "text",
            ):
                value_sql, value = ":v", self.owner.user_id
            elif table_name == "patients" and col.name == "id":
                value_sql, value = ":v", self.owner.patient_id
            elif (col.name in table.pk and not col.has_default) or (
                not col.nullable and not col.has_default
            ):
                value_sql, value = _value_for(self.session, table, col)
            if value_sql is None:
                continue
            key = f"p{i}"
            names.append(f'"{col.name}"')
            exprs.append(value_sql.replace(":v", f":{key}"))
            if ":v" in value_sql:
                params[key] = value
        returning = ", ".join(f'"{c.name}"' for c in table.columns)
        sql = (
            f'INSERT INTO "{self.schema}"."{table_name}" ({", ".join(names)}) '  # noqa: S608
            f"VALUES ({', '.join(exprs)}) RETURNING {returning}"
        )
        if not names:
            sql = f'INSERT INTO "{self.schema}"."{table_name}" DEFAULT VALUES RETURNING {returning}'  # noqa: S608
        row = self.session.execute(text(sql), params).mappings().one()
        self.rows[table_name] = dict(row)
        return self.rows[table_name]
