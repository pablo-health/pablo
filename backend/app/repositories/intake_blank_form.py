# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Blank-form repository — the practice's own empty paperwork.

Practice-level, so unlike every per-patient repository in this package no
method here names a principal. A blank form is the practice's stationery:
it holds nothing about anybody, the tenant schema is its boundary, and both
the clinician who uploads it and the patient who downloads it read the same
row through the same method.

That is why the read used by the portal is :meth:`get_finalized` rather
than something narrower. The narrowing that matters is which TABLE it
reads: a document id that names a patient's chart is not in here, so it
comes back ``None`` — the patient blank-form route cannot be pointed at a
chart even in principle.

Rows are plain ``dict[str, object]`` matching the column layout, the same
shape the other repositories in this package hand back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class IntakeBlankFormRepository(ABC):
    """Abstract base class for blank-form data access."""

    @abstractmethod
    def add(self, row: dict[str, object]) -> dict[str, object]:
        """Insert the placeholder row an upload starts with.

        ``finalized_at`` is NULL until the object is there, so an upload
        that was started and abandoned appears in no list.
        """

    @abstractmethod
    def mark_finalized(
        self, form_id: str, *, size_bytes: int, finalized_at: object
    ) -> dict[str, object] | None:
        """Stamp the real size and the finalize time. ``None`` if it is gone."""

    @abstractmethod
    def get(self, form_id: str) -> dict[str, object] | None:
        """One form whether or not its upload finished. ``None`` once deleted."""

    @abstractmethod
    def get_finalized(self, form_id: str) -> dict[str, object] | None:
        """One form whose upload finished, for anybody in the practice.

        What the portal's download route reads. ``None`` for a deleted row,
        an unfinished upload, and an id that names something else entirely
        — all three indistinguishable, so nothing here says what exists.
        """

    @abstractmethod
    def list_all(self) -> list[dict[str, object]]:
        """Every form still in use, newest first."""

    @abstractmethod
    def soft_delete(self, form_id: str, deleted_at: object) -> bool:
        """Tombstone a form the practice has stopped using.

        Never a hard delete: a published question may still name it, and an
        item pointing at a tombstone shows no download rather than a broken
        one.
        """


class InMemoryIntakeBlankFormRepository(IntakeBlankFormRepository):
    """In-memory repository for unit tests."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}

    def add(self, row: dict[str, object]) -> dict[str, object]:
        self.rows[str(row["id"])] = dict(row)
        return dict(row)

    def mark_finalized(
        self, form_id: str, *, size_bytes: int, finalized_at: object
    ) -> dict[str, object] | None:
        row = self.rows.get(form_id)
        if row is None or row.get("deleted_at") is not None:
            return None
        row["size_bytes"] = size_bytes
        row["finalized_at"] = finalized_at
        return dict(row)

    def get(self, form_id: str) -> dict[str, object] | None:
        row = self.rows.get(form_id)
        if row is None or row.get("deleted_at") is not None:
            return None
        return dict(row)

    def get_finalized(self, form_id: str) -> dict[str, object] | None:
        row = self.get(form_id)
        if row is None or row.get("finalized_at") is None:
            return None
        return row

    def list_all(self) -> list[dict[str, object]]:
        rows = [
            dict(row)
            for row in self.rows.values()
            if row.get("deleted_at") is None and row.get("finalized_at") is not None
        ]
        rows.sort(key=lambda row: (row["created_at"], str(row["id"])), reverse=True)  # type: ignore[index]
        return rows

    def soft_delete(self, form_id: str, deleted_at: object) -> bool:
        row = self.rows.get(form_id)
        if row is None or row.get("deleted_at") is not None:
            return False
        row["deleted_at"] = deleted_at
        return True


__all__ = [
    "InMemoryIntakeBlankFormRepository",
    "IntakeBlankFormRepository",
]
