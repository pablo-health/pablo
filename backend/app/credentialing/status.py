# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""How far through the checklist a clinician actually is, read off the record.

:func:`answered_keys` is the bridge between the question set and the tables it
writes into. Nothing here restates where a field lands — every lookup is
derived from that field's own ``target``, so a question retargeted in
``checklist.py`` reports against its new home with no edit here.

A field counts as answered when its target holds something:

* ``table.column`` on a one-row-per-clinician table — the column is not NULL.
* ``table.column`` on a repeating table — at least one of her rows has it.
* a bare table — she has at least one row in it.
* ``credential_disclosures`` — she has answered THAT question, not merely some
  question, so the key is matched.
* an upload — a document of that type exists in the vault.
* ``credential_confirmations`` — she has confirmed that field.

The result feeds :func:`app.credentialing.checklist.completion`, which reports per
tier. Nothing here produces a single overall figure, for the reason given
there: one number renders Tier-1-and-stop as half done.

:func:`current_values` is the same walk asking a different question. Where
``answered_keys`` resolves a target to "is anything in it", this resolves the
same target to "what is in it", so the Tier-0 confirm cards can show her the
value she is being asked about. They live in one module deliberately: two
resolvers in two places would eventually disagree about where a field lives,
and the one that disagreed silently would be this one — a card confirming a
column nobody is reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, select

from ..db.models import (
    Base,
    ClinicianProfileRow,
    ComplianceDocumentRow,
    CredentialDisclosureRow,
    PracticeBillingProfileRow,
)
from ..services.practice_billing_profile import SINGLETON_ID
from . import confirmations
from .checklist import CHECKLIST_FIELDS, ChecklistField, FieldKind, Tier

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: Tables holding exactly one row per clinician, keyed by ``user_id`` rather
#: than carrying one. A column on one of these is answered when it is not NULL;
#: on any other table, when at least one of her rows has it.
_SINGLE_ROW_TABLES: frozenset[str] = frozenset({"credential_government_ids", "clinician_profiles"})

#: Tables scoped to the practice rather than to a clinician. Their values are
#: shown in Tier 0 and confirmed there; the confirmation is the per-clinician
#: fact, so populated-ness is not asked of them.
_PRACTICE_SCOPED_TABLES: frozenset[str] = frozenset({"practice_billing_profile"})


def _table_and_column(field: ChecklistField) -> tuple[str, str | None]:
    table, _, column = field.target.partition(".")
    return table, column or None


def _has_document(session: Session, user_id: str, document_type: str) -> bool:
    """An upload is answered when a document of its type is in the vault.

    Keyed on ``document_type`` rather than on a foreign key from the question,
    because the vault is where a document lives whether it arrived through the
    checklist or through the compliance surface that predates it.
    """
    return (
        session.scalar(
            select(func.count())
            .select_from(ComplianceDocumentRow)
            .where(
                and_(
                    ComplianceDocumentRow.uploaded_by_user_id == user_id,
                    ComplianceDocumentRow.document_type == document_type,
                )
            )
        )
        or 0
    ) > 0


def _is_answered(
    session: Session,
    user_id: str,
    field: ChecklistField,
    *,
    answered_elsewhere: _AnsweredElsewhere,
) -> bool:
    table, column = _table_and_column(field)

    # Both of these are answered by the confirmation rather than by a column:
    # one has no home column anywhere, and the other's column belongs to the
    # practice, so confirming it is the per-clinician fact.
    if table == "credential_confirmations" or table in _PRACTICE_SCOPED_TABLES:
        return field.key in answered_elsewhere.confirmed
    if table == "credential_disclosures":
        return field.key in answered_elsewhere.disclosed
    if field.kind is FieldKind.UPLOAD and table == "compliance_documents":
        return _has_document(session, user_id, field.key)

    mapped = Base.metadata.tables.get(table)
    if mapped is None or "user_id" not in mapped.c:
        return False

    predicate = mapped.c.user_id == user_id
    if column is not None:
        predicate = and_(predicate, mapped.c[column].is_not(None))
    if table in _SINGLE_ROW_TABLES and column is None:
        # A bare single-row table would be "answered" the moment the row is
        # created, which happens on the first write to any of its columns.
        return False
    return (session.scalar(select(func.count()).select_from(mapped).where(predicate)) or 0) > 0


@dataclass(frozen=True)
class _AnsweredElsewhere:
    """The two key sets that answer a field without a column behind them.

    Loaded once per walk rather than asked per field. Both tables are keyed by
    the field's own key, so a COUNT per field was one query per card — fifteen
    round trips to learn what two selects already know.
    """

    confirmed: frozenset[str]
    disclosed: frozenset[str]

    @classmethod
    def load(cls, session: Session, user_id: str) -> _AnsweredElsewhere:
        disclosed = session.scalars(
            select(CredentialDisclosureRow.question_key).where(
                CredentialDisclosureRow.user_id == user_id
            )
        ).all()
        return cls(
            confirmed=frozenset(row.field_key for row in confirmations.list_for(session, user_id)),
            disclosed=frozenset(disclosed),
        )


def answered_keys(session: Session, user_id: str) -> set[str]:
    """Which of the checklist's items this clinician has an answer on file for.

    Reads the record rather than a progress column, so a fact entered through
    any other surface — the compliance vault, the billing profile, an importer
    — counts. The checklist exists to avoid asking twice; a progress column it
    kept for itself would ask again.
    """
    answered_elsewhere = _AnsweredElsewhere.load(session, user_id)
    return {
        f.key
        for f in CHECKLIST_FIELDS
        if _is_answered(session, user_id, f, answered_elsewhere=answered_elsewhere)
    }


# --- What is actually in the record --------------------------------------
#
# Tier 0 asks her to agree with a value, which means the value has to be on
# the card. Everything below resolves a field's own ``target`` to the value
# sitting in it, and renders it for display — never for arithmetic, and never
# back into the column it came from.

#: Columns this resolver must never read, whatever a field targets.
#:
#: These have exactly one reader — :func:`app.credentialing.government_ids.
#: view_identifiers` — and it writes an audit row naming the fields it
#: decrypted. A generic value-resolver that could reach them would be a second
#: path to the most sensitive columns in the schema, and an unaudited one.
#:
#: No Tier-0 field targets one today. The list is here so that the day one
#: does, it shows nothing rather than quietly shipping ciphertext to a browser
#: — which is what an unguarded ``getattr`` would do, since nothing here
#: decrypts. The suffix rule below backs it up: a column added later with the
#: same ``_encrypted`` convention is refused without anyone remembering to
#: come back here, and the named set survives a column that breaks convention.
_REFUSED_COLUMNS: frozenset[str] = frozenset(
    {
        "ssn_encrypted",
        "dob_encrypted",
        "tax_id_encrypted",
        "routing_number_encrypted",
        "account_number_encrypted",
    }
)


def _is_refused(column: str | None) -> bool:
    return column is not None and (column in _REFUSED_COLUMNS or column.endswith("_encrypted"))


def _render(value: object) -> str | None:
    """What a card shows, or ``None`` when there is genuinely nothing behind it.

    Empty is not a value. A blank string or an empty list reads on screen as a
    confirmed fact that happens to look empty, so both come back as ``None``
    and the card says "Nothing on file" — which is true, and is the thing she
    needs to know.
    """
    if value is None:
        return None
    # Before any numeric branch: bool is a subclass of int, and "1" is not an
    # answer to a yes-or-no question.
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list | tuple):
        return ", ".join(p for p in (_render(v) for v in value) if p) or None
    # datetime before date, and folded into it: datetime IS a date subclass,
    # so the order matters and the rendering does not.
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _presented_values(session: Session, user_id: str) -> dict[str, str | None]:
    """The last value she was shown for each field she has answered on.

    One query for the whole tier rather than one per card, and it is the one
    ``confirmations`` already owns — the reader here does not need its own
    idea of how a confirmation row is found.
    """
    return {row.field_key: row.presented_value for row in confirmations.list_for(session, user_id)}


def _current_value(
    field: ChecklistField,
    *,
    rows: dict[str, object],
    presented: dict[str, str | None],
) -> str | None:
    table, column = _table_and_column(field)

    # The fields with no home column anywhere else. The confirmation row IS
    # the value for these, so what she was last shown is what is on file.
    if table == "credential_confirmations":
        return _render(presented.get(field.key))

    if _is_refused(column):
        return None

    row = rows.get(table)
    if row is None or column is None:
        return None
    return _render(getattr(row, column, None))


def current_values(session: Session, user_id: str) -> dict[str, str]:
    """What the record currently holds for each Tier-0 field, ready to display.

    A key is present only when there is something to show, mirroring
    :func:`answered_keys`: absent means nothing on file, and no caller has to
    tell an empty string apart from an unset column.

    Tier 0 only, and deliberately. The other tiers are questions with empty
    boxes, so there is nothing to pre-fill — and their targets sit on
    ``credential_government_ids``, the table the encrypted identifiers live in.
    Not walking them keeps this resolver away from that table entirely, which
    is a stronger guarantee than refusing its columns one at a time (though
    :data:`_REFUSED_COLUMNS` does that too).

    Three queries for the whole tier, however many cards it grows to: the two
    rows every field reads from, and the confirmations.
    """
    presented = _presented_values(session, user_id)
    rows: dict[str, object] = {}
    profile = session.get(ClinicianProfileRow, user_id)
    if profile is not None:
        rows["clinician_profiles"] = profile
    billing = session.get(PracticeBillingProfileRow, SINGLETON_ID)
    if billing is not None:
        rows["practice_billing_profile"] = billing

    values: dict[str, str] = {}
    for field in CHECKLIST_FIELDS:
        if field.tier is not Tier.CONFIRM:
            continue
        rendered = _current_value(field, rows=rows, presented=presented)
        if rendered is not None:
            values[field.key] = rendered
    return values
