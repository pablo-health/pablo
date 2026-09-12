# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""How far through the intake a clinician actually is, read off the record.

:func:`answered_keys` is the bridge between the question set and the tables it
writes into. Nothing here restates where a field lands — every lookup is
derived from that field's own ``target``, so a question retargeted in
``intake.py`` reports against its new home with no edit here.

A field counts as answered when its target holds something:

* ``table.column`` on a one-row-per-clinician table — the column is not NULL.
* ``table.column`` on a repeating table — at least one of her rows has it.
* a bare table — she has at least one row in it.
* ``credential_disclosures`` — she has answered THAT question, not merely some
  question, so the key is matched.
* an upload — a document of that type exists in the vault.
* ``credential_confirmations`` — she has confirmed that field.

The result feeds :func:`app.credentialing.intake.completion`, which reports per
tier. Nothing here produces a single overall figure, for the reason given
there: one number renders Tier-1-and-stop as half done.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import and_, func, select

from ..db.models import (
    Base,
    ComplianceDocumentRow,
    CredentialConfirmationRow,
    CredentialDisclosureRow,
)
from .intake import INTAKE_FIELDS, FieldKind, IntakeField

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


def _table_and_column(field: IntakeField) -> tuple[str, str | None]:
    table, _, column = field.target.partition(".")
    return table, column or None


def _has_document(session: Session, user_id: str, document_type: str) -> bool:
    """An upload is answered when a document of its type is in the vault.

    Keyed on ``document_type`` rather than on a foreign key from the question,
    because the vault is where a document lives whether it arrived through the
    intake or through the compliance surface that predates it.
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


def _is_answered(session: Session, user_id: str, field: IntakeField) -> bool:  # noqa: PLR0911
    table, column = _table_and_column(field)

    if table == "credential_confirmations":
        return _confirmed(session, user_id, field.key)
    if table == "credential_disclosures":
        return _disclosed(session, user_id, field.key)
    if field.kind is FieldKind.UPLOAD and table == "compliance_documents":
        return _has_document(session, user_id, field.key)
    if table in _PRACTICE_SCOPED_TABLES:
        return _confirmed(session, user_id, field.key)

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


def _confirmed(session: Session, user_id: str, field_key: str) -> bool:
    return (
        session.scalar(
            select(func.count())
            .select_from(CredentialConfirmationRow)
            .where(
                and_(
                    CredentialConfirmationRow.user_id == user_id,
                    CredentialConfirmationRow.field_key == field_key,
                )
            )
        )
        or 0
    ) > 0


def _disclosed(session: Session, user_id: str, question_key: str) -> bool:
    return (
        session.scalar(
            select(func.count())
            .select_from(CredentialDisclosureRow)
            .where(
                and_(
                    CredentialDisclosureRow.user_id == user_id,
                    CredentialDisclosureRow.question_key == question_key,
                )
            )
        )
        or 0
    ) > 0


def answered_keys(session: Session, user_id: str) -> set[str]:
    """Which of the intake's questions this clinician has an answer on file for.

    Reads the record rather than a progress column, so a fact entered through
    any other surface — the compliance vault, the billing profile, an importer
    — counts. The intake exists to avoid asking twice; a progress column it
    kept for itself would ask again.
    """
    return {f.key for f in INTAKE_FIELDS if _is_answered(session, user_id, f)}
