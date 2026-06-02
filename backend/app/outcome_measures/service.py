# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Business logic for the outcome measures API.

Orchestrates validation (via the instrument registry), persistence (via the
repository), and read-path filtering (soft-delete exclusion, instrument filter).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, cast

from ..repositories.outcome_measure import PatientOutcomeAccessDeniedError
from ..utcnow import utc_now
from .instruments import (
    compute_total,
    get_instrument,
    is_complete,
    severity_label,
    validate_item_scores,
)
from .schemas import CreateOutcomeMeasureRequest, OutcomeMeasureResponse

if TYPE_CHECKING:
    from ..repositories.outcome_measure import OutcomeMeasureRepository


class UnknownInstrumentError(ValueError):
    """Raised when the requested instrument code is not in the registry."""


class OutcomeMeasureNotFoundError(LookupError):
    """Raised when an outcome measure row cannot be found or is inaccessible."""


class OutcomeMeasureService:
    """Read/write operations for outcome measures."""

    def __init__(self, repo: OutcomeMeasureRepository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_response(row: dict[str, object]) -> OutcomeMeasureResponse:
        """Build an API response from a repository row dict."""
        total_raw = row.get("total_score")
        total: int | None = int(total_raw) if isinstance(total_raw, int) else None
        instrument_code = str(row["instrument"])
        defn = get_instrument(instrument_code)
        severity: str | None = None
        if defn is not None and total is not None:
            severity = severity_label(defn, total)

        item_scores_raw = row.get("item_scores")
        item_scores: dict[str, int] | None = None
        if isinstance(item_scores_raw, dict):
            item_scores = cast("dict[str, int]", item_scores_raw)

        item_citations_raw = row.get("item_citations")
        item_citations: dict[str, object] | None = (
            cast("dict[str, object]", item_citations_raw)
            if isinstance(item_citations_raw, dict)
            else None
        )

        return OutcomeMeasureResponse(
            id=str(row["id"]),
            patient_id=str(row["patient_id"]),
            session_id=str(row["session_id"]) if row.get("session_id") else None,
            appointment_id=(str(row["appointment_id"]) if row.get("appointment_id") else None),
            instrument=instrument_code,
            total_score=total,
            item_scores=item_scores,
            is_complete=bool(row.get("is_complete", False)),
            source=str(row["source"]),
            item_citations=item_citations,
            administered_at=row["administered_at"],  # type: ignore[arg-type]
            created_by=str(row["created_by"]),
            created_at=row["created_at"],  # type: ignore[arg-type]
            updated_at=row["updated_at"],  # type: ignore[arg-type]
            severity=severity,
        )

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def create(
        self,
        patient_id: str,
        request: CreateOutcomeMeasureRequest,
        user_id: str,
    ) -> OutcomeMeasureResponse:
        """Validate and persist a new outcome measure row.

        Raises
        ------
        UnknownInstrumentError
            When the instrument code is not in the registry and item_scores
            are provided (we need the definition to validate them).
        InstrumentValidationError
            When item_scores fail the instrument's constraints.
        ValueError
            When neither item_scores nor total_score are provided.
        OutcomeMeasureNotFoundError
            When the caller has no access grant for the patient. Surfaced as
            "not found" (not "forbidden") so the API can't be used as an
            existence oracle — the same way the notes service converts a
            denied write into ``NoteNotFoundError``.
        """
        if request.item_scores is None and request.total_score is None:
            raise ValueError("At least one of item_scores or total_score must be provided.")

        now = utc_now()
        total: int | None = request.total_score
        complete: bool = False
        item_scores: dict[str, int] | None = request.item_scores

        if item_scores is not None:
            defn = get_instrument(request.instrument)
            if defn is None:
                # Unknown instrument with item_scores — can't validate.
                raise UnknownInstrumentError(
                    f"Unknown instrument {request.instrument!r}. "
                    "Provide item_scores only for registered instruments, "
                    "or omit them and supply total_score directly."
                )
            validate_item_scores(defn, item_scores)
            total = compute_total(defn, item_scores)
            complete = is_complete(defn, item_scores)

        row: dict[str, object] = {
            "id": str(uuid.uuid4()),
            "patient_id": patient_id,
            "session_id": request.session_id,
            "appointment_id": request.appointment_id,
            "instrument": request.instrument,
            "total_score": total,
            "item_scores": item_scores,
            "is_complete": complete,
            "source": request.source.value,
            "item_citations": None,
            "administered_at": request.administered_at,
            "created_by": user_id,
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        try:
            saved = self._repo.add(row, user_id)
        except PatientOutcomeAccessDeniedError as exc:
            raise OutcomeMeasureNotFoundError(patient_id) from exc
        return self._build_response(saved)

    def soft_delete(self, measure_id: str, user_id: str) -> None:
        """Soft-delete a measure by setting ``deleted_at``.

        Raises :class:`OutcomeMeasureNotFoundError` if not found or
        inaccessible.
        """
        existing = self._repo.get(measure_id, user_id)
        if existing is None:
            raise OutcomeMeasureNotFoundError(measure_id)
        now = utc_now()
        existing["deleted_at"] = now
        existing["updated_at"] = now
        self._repo.update(existing, user_id)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get(self, measure_id: str, user_id: str) -> OutcomeMeasureResponse:
        """Fetch a single live (non-deleted) measure.

        Raises :class:`OutcomeMeasureNotFoundError` if absent, soft-deleted,
        or inaccessible.
        """
        row = self._repo.get(measure_id, user_id)
        if row is None or row.get("deleted_at") is not None:
            raise OutcomeMeasureNotFoundError(measure_id)
        return self._build_response(row)

    def list_for_patient(
        self,
        patient_id: str,
        user_id: str,
        instrument: str | None = None,
    ) -> list[OutcomeMeasureResponse]:
        """List live measures for a patient, ordered by administered_at ascending.

        Parameters
        ----------
        instrument:
            When provided, restricts results to that instrument code.
        """
        rows = self._repo.list_by_patient(patient_id, user_id, instrument=instrument)
        live = [r for r in rows if r.get("deleted_at") is None]
        live.sort(key=lambda r: r["administered_at"])  # type: ignore[arg-type, return-value]
        return [self._build_response(r) for r in live]
