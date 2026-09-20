# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL PatientIntakeSignatureRepository implementation.

Tenant scope is the session's ``search_path``, set before the request
reaches here. Within a tenant, the patient-principal methods filter on the
id their caller took off the authenticated principal, backed by the
``app.current_patient_id`` policy underneath — the separation the abstract
base describes.

Row security is the floor, not the ceiling. Every query below also carries
its own predicate, so a missing GUC is a wrong answer from the database
rather than a wide one from here.

**The insert is the only write.** There is no update and no delete on this
table, which is what makes a stored signature a record rather than a
current value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ...db.models import PatientIntakeSignatureRow
from ..patient_intake_signature import (
    PatientIntakeSignatureRepository,
    SignatureExistsError,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: The partial unique index that carries "one role signs one item once".
_LIVE_INDEX = "uq_patient_intake_signatures_live"


def _to_dict(row: PatientIntakeSignatureRow) -> dict[str, object]:
    return {
        "id": row.id,
        "assignment_id": row.assignment_id,
        "patient_id": row.patient_id,
        "item_id": row.item_id,
        "document_version_id": row.document_version_id,
        "document_digest": row.document_digest,
        "signer_role": row.signer_role,
        "signer_typed_name": row.signer_typed_name,
        "consent_statement_version": row.consent_statement_version,
        "signed_at": row.signed_at,
        "auth_strength": row.auth_strength,
        "session_id": row.session_id,
        "ip": row.ip,
        "user_agent": row.user_agent,
        "evidence_digest": row.evidence_digest,
        "superseded_at": row.superseded_at,
        "created_at": row.created_at,
    }


class PostgresPatientIntakeSignatureRepository(PatientIntakeSignatureRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- patient side ---

    def add(self, row: dict[str, object]) -> dict[str, object]:
        """Record one signature, or raise on a second one for the same role.

        The caller has already looked for an existing signature and refused
        the ordinary second click. What survives that check is a genuine
        race — two requests in flight for the same item at the same instant
        — and only the index can arbitrate it, so the integrity error is
        translated here rather than leaving the caller to parse a driver
        message.

        The flush is what makes the refusal arrive now rather than at
        commit, which matters because the caller writes the item's answer
        immediately afterwards and must not write one for a signature that
        was refused.
        """
        orm_row = PatientIntakeSignatureRow(
            id=str(row["id"]),
            assignment_id=str(row["assignment_id"]),
            patient_id=str(row["patient_id"]),
            item_id=str(row["item_id"]),
            document_version_id=str(row["document_version_id"]),
            document_digest=str(row["document_digest"]),
            signer_role=str(row["signer_role"]),
            signer_typed_name=str(row["signer_typed_name"]),
            consent_statement_version=str(row["consent_statement_version"]),
            signed_at=row["signed_at"],  # type: ignore[arg-type]
            auth_strength=str(row["auth_strength"]),
            session_id=_optional(row.get("session_id")),
            ip=_optional(row.get("ip")),
            user_agent=_optional(row.get("user_agent")),
            evidence_digest=str(row["evidence_digest"]),
            superseded_at=None,
            created_at=row["created_at"],  # type: ignore[arg-type]
        )
        self._session.add(orm_row)
        try:
            self._session.flush()
        except IntegrityError as exc:
            if _LIVE_INDEX in str(exc):
                raise SignatureExistsError(str(row["item_id"])) from exc
            raise
        return _to_dict(orm_row)

    def list_live_for_assignment(
        self, assignment_id: str, patient_id: str
    ) -> list[dict[str, object]]:
        rows = (
            self._session.execute(
                select(PatientIntakeSignatureRow)
                .where(
                    PatientIntakeSignatureRow.assignment_id == assignment_id,
                    PatientIntakeSignatureRow.patient_id == patient_id,
                    PatientIntakeSignatureRow.superseded_at.is_(None),
                )
                .order_by(
                    PatientIntakeSignatureRow.signed_at,
                    PatientIntakeSignatureRow.id,
                )
            )
            .scalars()
            .all()
        )
        return [_to_dict(row) for row in rows]

    def get_live(
        self, assignment_id: str, patient_id: str, item_id: str, signer_role: str
    ) -> dict[str, object] | None:
        row = (
            self._session.execute(
                select(PatientIntakeSignatureRow).where(
                    PatientIntakeSignatureRow.assignment_id == assignment_id,
                    PatientIntakeSignatureRow.patient_id == patient_id,
                    PatientIntakeSignatureRow.item_id == item_id,
                    PatientIntakeSignatureRow.signer_role == signer_role,
                    PatientIntakeSignatureRow.superseded_at.is_(None),
                )
            )
            .scalars()
            .first()
        )
        return _to_dict(row) if row else None


def _optional(value: object) -> str | None:
    return str(value) if value is not None else None
