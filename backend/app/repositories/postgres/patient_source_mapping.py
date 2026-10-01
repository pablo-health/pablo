# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL patient source mapping repository implementation.

Writes are insert-on-conflict against the practice's key, never read-then-
write: two requests answering or adopting the same identifier at once both
land, and the row ends up as the newer of the two says.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert

from ...db.models import PatientSourceMappingRow
from ...patients.identifiers import identifier_digest, is_calendar_scope
from ...utcnow import utc_now
from ..patient_source_mapping import PatientSourceMapping, PatientSourceMappingRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_KEY = ("scope", "source", "source_identifier")
_PRACTICE_ROWS = text("scope IS NOT NULL")


class PostgresPatientSourceMappingRepository(PatientSourceMappingRepository):
    """PostgreSQL implementation of PatientSourceMappingRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_by_source(self, scope: str, source: str) -> list[PatientSourceMapping]:
        rows = (
            self._session.execute(
                select(PatientSourceMappingRow)
                .where(
                    PatientSourceMappingRow.scope == scope,
                    PatientSourceMappingRow.source == source,
                )
                # A row this session loaded earlier may have been replaced
                # underneath by an upsert; read what the table says now.
                .execution_options(populate_existing=True)
            )
            .scalars()
            .all()
        )
        return [_row_to_mapping(r) for r in rows]

    def save(self, mapping: PatientSourceMapping) -> None:
        if mapping.created_at is None:
            mapping.created_at = utc_now()
        values = _values(mapping)
        statement = insert(PatientSourceMappingRow).values(values)
        self._session.execute(
            statement.on_conflict_do_update(
                index_elements=list(_KEY),
                index_where=_PRACTICE_ROWS,
                set_={k: statement.excluded[k] for k in values if k not in {"doc_id", *_KEY}},
            )
        )
        self._session.flush()

    def adopt_legacy(self, user_id: str, source: str, scope: str) -> int:
        legacy = (
            self._session.execute(
                select(PatientSourceMappingRow).where(
                    PatientSourceMappingRow.scope.is_(None),
                    PatientSourceMappingRow.user_id == user_id,
                    PatientSourceMappingRow.source == source,
                )
            )
            .scalars()
            .all()
        )
        gave_way: list[str] = []
        for old in legacy:
            adopted = PatientSourceMapping(
                scope=scope,
                source=source,
                identifier_digest=identifier_digest(old.source_identifier),
                patient_id=old.patient_id,
                answered_by_user_id=old.user_id,
                created_at=old.created_at,
                answer=old.answer,
                answered_title=old.answered_title,
                session_clinician_user_id=old.user_id if is_calendar_scope(scope) else None,
            )
            values = _values(adopted)
            statement = insert(PatientSourceMappingRow).values(values)
            # The newer answer stands. ``created_at`` is compared as the rows
            # hold it; an undated one is the oldest.
            stood = self._session.execute(
                statement.on_conflict_do_update(
                    index_elements=list(_KEY),
                    index_where=_PRACTICE_ROWS,
                    set_={k: statement.excluded[k] for k in values if k not in {"doc_id", *_KEY}},
                    where=(
                        PatientSourceMappingRow.created_at.is_(None)
                        | (statement.excluded.created_at > PatientSourceMappingRow.created_at)
                    ),
                ).returning(PatientSourceMappingRow.doc_id)
            ).scalar()
            if stood is None:
                gave_way.append(adopted.doc_id)
            # A plain DELETE: another request may have adopted this row
            # already, and finding it gone is not an error.
            self._session.execute(
                delete(PatientSourceMappingRow).where(
                    PatientSourceMappingRow.doc_id == old.doc_id,
                    PatientSourceMappingRow.scope.is_(None),
                )
            )
        if legacy:
            self._session.flush()
            # Ids only: a new-shape id carries the digest, never the identifier.
            logger.info(
                "Adopted %d remembered answers into the practice's; %d gave way to a newer "
                "answer already held: %s",
                len(legacy),
                len(gave_way),
                gave_way,
            )
        return len(legacy)


def _values(mapping: PatientSourceMapping) -> dict[str, object]:
    return {
        "doc_id": mapping.doc_id,
        "scope": mapping.scope,
        "source": mapping.source,
        "source_identifier": mapping.identifier_digest,
        # Filled with the answerer until the column goes: it is still required.
        "user_id": mapping.answered_by_user_id,
        "answered_by_user_id": mapping.answered_by_user_id,
        "session_clinician_user_id": mapping.session_clinician_user_id,
        "answer": mapping.answer,
        "patient_id": mapping.patient_id,
        "answered_title": mapping.answered_title,
        "created_at": mapping.created_at,
    }


def _row_to_mapping(row: PatientSourceMappingRow) -> PatientSourceMapping:
    return PatientSourceMapping(
        scope=row.scope or "",
        source=row.source,
        identifier_digest=row.source_identifier,
        patient_id=row.patient_id,
        answered_by_user_id=row.answered_by_user_id or row.user_id,
        created_at=row.created_at,
        answer=row.answer,
        answered_title=row.answered_title,
        session_clinician_user_id=row.session_clinician_user_id,
    )
