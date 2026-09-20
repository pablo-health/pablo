# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Intake signature repository — what a patient signed, and when.

One table, and the narrowest interface in this package: a signature is
written once and read back. **There is no update and no delete**, and that
is the model rather than an omission — a signature that stops counting is
superseded by a later row, so nothing here can rewrite what somebody
already agreed to. The route layer never asks for such a method because
there is none to ask for.

Like the assignment repository beside it, every method names which principal
is asking, so neither can be called with the other's notion of who is
asking. Today every method here is the patient's: they reach their own rows
through the ``app.current_patient_id`` policy, with the id taken off the
authenticated principal. A clinician read of the same rows lands with the
screen that needs one and brings its own method, named for that principal
and scoped by the ``has_patient_access`` grant the rest of the chart uses —
the row policy for it is already in place.

"Live" throughout means ``superseded_at IS NULL`` — the same predicate the
partial unique index is built on, which is what makes at most one live row
per assignment, item and role.

Rows are plain ``dict[str, object]`` matching the column layout, the same
shape the other repositories in this package hand back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class PatientIntakeSignatureRepository(ABC):
    """Abstract base class for intake signature data access."""

    # --- patient side ---

    @abstractmethod
    def add(self, row: dict[str, object]) -> dict[str, object]:
        """Record one signature.

        The caller has already checked everything that could refuse it. What
        this cannot check is a simultaneous second attempt on the same
        assignment, item and role — the partial unique index arbitrates
        that, and the caller turns the integrity error into a 409.
        """

    @abstractmethod
    def list_live_for_assignment(
        self, assignment_id: str, patient_id: str
    ) -> list[dict[str, object]]:
        """The calling patient's live signatures on one of their own forms.

        Oldest first, so a document asking for two signatures reads in the
        order they were given.
        """

    @abstractmethod
    def get_live(
        self, assignment_id: str, patient_id: str, item_id: str, signer_role: str
    ) -> dict[str, object] | None:
        """This role's live signature on this item, or ``None``.

        What answers "has this already been signed" before a write is
        attempted, so the ordinary second-click case is a 409 with a sentence
        rather than a database error the caller has to interpret.
        """


class InMemoryPatientIntakeSignatureRepository(PatientIntakeSignatureRepository):
    """In-memory repository for unit tests.

    The partial unique index has a counterpart here: :meth:`add` raises
    :class:`SignatureExistsError` on a second live row for the same
    assignment, item and role, so a unit test meets the same refusal the
    database gives.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}

    # --- patient side ---

    def add(self, row: dict[str, object]) -> dict[str, object]:
        clash = self.get_live(
            str(row["assignment_id"]),
            str(row["patient_id"]),
            str(row["item_id"]),
            str(row["signer_role"]),
        )
        if clash is not None:
            raise SignatureExistsError(str(clash["id"]))
        self.rows[str(row["id"])] = dict(row)
        return dict(row)

    def list_live_for_assignment(
        self, assignment_id: str, patient_id: str
    ) -> list[dict[str, object]]:
        return self._live(
            lambda row: (
                str(row["assignment_id"]) == assignment_id and str(row["patient_id"]) == patient_id
            )
        )

    def get_live(
        self, assignment_id: str, patient_id: str, item_id: str, signer_role: str
    ) -> dict[str, object] | None:
        found = self._live(
            lambda row: (
                str(row["assignment_id"]) == assignment_id
                and str(row["patient_id"]) == patient_id
                and str(row["item_id"]) == item_id
                and str(row["signer_role"]) == signer_role
            )
        )
        return found[0] if found else None

    # --- helpers ---

    def _live(self, matches: Callable[[dict[str, object]], bool]) -> list[dict[str, object]]:
        rows = [
            dict(row)
            for row in self.rows.values()
            if row.get("superseded_at") is None and matches(row)
        ]
        rows.sort(key=lambda row: (row["signed_at"], str(row["id"])))  # type: ignore[index]
        return rows


class SignatureExistsError(RuntimeError):
    """A live signature already exists for this assignment, item and role.

    Raised by the repository rather than surfaced as an integrity error,
    because the caller's response to it is a 409 with a sentence the patient
    reads — which is not the response to any other failure on this table.
    """


__all__ = [
    "InMemoryPatientIntakeSignatureRepository",
    "PatientIntakeSignatureRepository",
    "SignatureExistsError",
]
