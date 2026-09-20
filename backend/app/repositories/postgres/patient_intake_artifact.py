# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL PatientIntakeArtifactRepository implementation.

Tenant scope is the session's ``search_path``, set before the request
reaches here. Within a tenant the two principals are separated as the
abstract base describes: the patient methods filter on the id their caller
took off the authenticated principal, backed by the
``app.current_patient_id`` policy underneath; the clinician method asks the
schema-local ``has_patient_access`` function the rest of the chart uses.

Row security is the floor, not the ceiling. Every query below also carries
its own predicate, so a missing GUC is a wrong answer from the database
rather than a wide one from here.

Two integrity errors are translated rather than left to the caller to
parse, because each has a sentence of its own on the screen: one file
answers one question, and a card has one front and one back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Uuid, bindparam, select, text
from sqlalchemy.exc import IntegrityError

from ...db.models import PatientIntakeArtifactRow
from ..patient_intake_artifact import (
    ArtifactSlotTakenError,
    DocumentAlreadyAttachedError,
    PatientIntakeArtifactRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_HAS_PATIENT_ACCESS_SQL = text("SELECT has_patient_access(:pid, :uid)").bindparams(
    bindparam("pid", type_=Uuid(as_uuid=False)),
    bindparam("uid", type_=String()),
)

#: The partial unique index that carries "one front, one back".
_SIDE_INDEX = "uq_patient_intake_artifacts_side"


def _to_dict(row: PatientIntakeArtifactRow) -> dict[str, object]:
    return {
        "id": row.id,
        "assignment_id": row.assignment_id,
        "patient_id": row.patient_id,
        "item_id": row.item_id,
        "document_id": row.document_id,
        "side": row.side,
        "created_at": row.created_at,
    }


class PostgresPatientIntakeArtifactRepository(PatientIntakeArtifactRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- patient side ---

    def add(self, row: dict[str, object]) -> dict[str, object]:
        """Attach one document, or raise on a clash the caller answers for.

        The flush is what makes a refusal arrive now rather than at commit,
        which matters because the caller rewrites the question's answer
        immediately afterwards and must not write one for an attachment
        that was refused.
        """
        side = row.get("side")
        orm_row = PatientIntakeArtifactRow(
            id=str(row["id"]),
            assignment_id=str(row["assignment_id"]),
            patient_id=str(row["patient_id"]),
            item_id=str(row["item_id"]),
            document_id=str(row["document_id"]),
            side=str(side) if side is not None else None,
            created_at=row["created_at"],  # type: ignore[arg-type]
        )
        self._session.add(orm_row)
        try:
            self._session.flush()
        except IntegrityError as exc:
            message = str(exc)
            if _SIDE_INDEX in message:
                raise ArtifactSlotTakenError(str(side)) from exc
            if "document_id" in message:
                raise DocumentAlreadyAttachedError(str(row["document_id"])) from exc
            raise
        return _to_dict(orm_row)

    def list_for_assignment(self, assignment_id: str, patient_id: str) -> list[dict[str, object]]:
        rows = (
            self._session.execute(
                select(PatientIntakeArtifactRow)
                .where(
                    PatientIntakeArtifactRow.assignment_id == assignment_id,
                    PatientIntakeArtifactRow.patient_id == patient_id,
                )
                .order_by(
                    PatientIntakeArtifactRow.created_at,
                    PatientIntakeArtifactRow.id,
                )
            )
            .scalars()
            .all()
        )
        return [_to_dict(row) for row in rows]

    def get(self, artifact_id: str, patient_id: str) -> dict[str, object] | None:
        row = (
            self._session.execute(
                select(PatientIntakeArtifactRow).where(
                    PatientIntakeArtifactRow.id == artifact_id,
                    PatientIntakeArtifactRow.patient_id == patient_id,
                )
            )
            .scalars()
            .first()
        )
        return _to_dict(row) if row else None

    def delete(self, artifact_id: str, patient_id: str) -> bool:
        """Remove one row, if it is this patient's.

        A select-then-delete rather than a bare DELETE: the caller has to
        know whether a row went, and the ORM delete's ``rowcount`` is not a
        typed part of the Result protocol. Reading it first is also what
        keeps the refusal shaped like every other one on this surface — no
        such artifact of yours and no such artifact answer the same way.
        """
        row = (
            self._session.execute(
                select(PatientIntakeArtifactRow).where(
                    PatientIntakeArtifactRow.id == artifact_id,
                    PatientIntakeArtifactRow.patient_id == patient_id,
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    # --- clinician side ---

    def list_for_clinician(self, assignment_id: str, user_id: str) -> list[dict[str, object]]:
        rows = (
            self._session.execute(
                select(PatientIntakeArtifactRow)
                .where(PatientIntakeArtifactRow.assignment_id == assignment_id)
                .order_by(
                    PatientIntakeArtifactRow.created_at,
                    PatientIntakeArtifactRow.id,
                )
            )
            .scalars()
            .all()
        )
        return [_to_dict(row) for row in rows if self._has_access(row.patient_id, user_id)]

    def _has_access(self, patient_id: str, user_id: str) -> bool:
        result = self._session.execute(
            _HAS_PATIENT_ACCESS_SQL, {"pid": patient_id, "uid": user_id}
        ).scalar()
        return bool(result)


__all__ = ["PostgresPatientIntakeArtifactRepository"]
