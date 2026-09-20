# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Intake packet repository — the practice's own forms, across three tables.

One repository for templates, versions and items rather than three, because
nothing ever wants one without the others: a version is meaningless outside
its template and an item is meaningless outside its version. Splitting them
would buy three interfaces that are always used together and a caller that
has to keep them consistent.

None of these rows belongs to a patient or to a clinician, so there is no
``has_patient_access`` call anywhere here and no ``user_id`` predicate. The
tenant schema is the boundary — the session is already pointed at one
practice, and every query below is scoped by being on that session.

Rows are plain ``dict[str, object]`` matching the column layout, the same
shape the other repositories in this package hand back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


class IntakePacketRepository(ABC):
    """Abstract base class for intake packet data access."""

    # --- templates ---

    @abstractmethod
    def add_template(self, row: dict[str, object]) -> dict[str, object]:
        """Insert a template."""

    @abstractmethod
    def list_templates(self, *, include_archived: bool = False) -> list[dict[str, object]]:
        """Every template the practice has, oldest first."""

    @abstractmethod
    def get_template(self, template_id: str) -> dict[str, object] | None:
        """One template, or ``None`` if there is no such id."""

    @abstractmethod
    def update_template(
        self,
        template_id: str,
        *,
        name: str | None = None,
        archived_at: datetime | None = None,
        unarchive: bool = False,
    ) -> dict[str, object] | None:
        """Rename, archive or restore a template.

        ``archived_at`` archives; ``unarchive`` clears it. They are separate
        arguments because ``None`` already means "leave it alone", and a
        single nullable argument could not say "clear this".
        """

    # --- versions ---

    @abstractmethod
    def add_version(self, row: dict[str, object]) -> dict[str, object]:
        """Insert a version."""

    @abstractmethod
    def list_versions(self, template_id: str) -> list[dict[str, object]]:
        """A template's versions, newest number first."""

    @abstractmethod
    def get_version(self, version_id: str) -> dict[str, object] | None:
        """One version, or ``None``."""

    @abstractmethod
    def latest_version(self, template_id: str) -> dict[str, object] | None:
        """The highest-numbered version of a template, or ``None``."""

    @abstractmethod
    def mark_published(
        self, version_id: str, published_at: datetime, published_by: str
    ) -> dict[str, object] | None:
        """Freeze a version. Returns ``None`` if there is no such version."""

    # --- items ---

    @abstractmethod
    def list_items(self, version_id: str) -> list[dict[str, object]]:
        """A version's items, in the order the patient sees them."""

    @abstractmethod
    def replace_items(
        self, version_id: str, rows: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        """Swap a version's whole item list for a new one.

        Wholesale rather than per-item because the editor sends an ordered
        list and ``position`` is unique per version: reconciling in place
        would need a two-phase shuffle to avoid colliding with the positions
        it is about to vacate.

        Callers are responsible for refusing this on a published version;
        the repository does not know that rule. See
        :class:`app.services.intake_packet_service.IntakePacketService`.
        """

    @abstractmethod
    def set_item_config(self, item_id: str, config: dict[str, object]) -> dict[str, object] | None:
        """Rewrite one item's configuration in place. ``None`` if no such id.

        Narrower than :meth:`replace_items` on purpose. The one caller is
        the publisher pinning a consent item's document version, and going
        through the wholesale swap would give every item on the version a
        new id — ids that an assignment's saved answers point at.
        """


class InMemoryIntakePacketRepository(IntakePacketRepository):
    """In-memory repository for unit tests."""

    def __init__(self) -> None:
        self.templates: dict[str, dict[str, object]] = {}
        self.versions: dict[str, dict[str, object]] = {}
        self.items: dict[str, list[dict[str, object]]] = {}

    # --- templates ---

    def add_template(self, row: dict[str, object]) -> dict[str, object]:
        self.templates[str(row["id"])] = dict(row)
        return dict(row)

    def list_templates(self, *, include_archived: bool = False) -> list[dict[str, object]]:
        rows = [
            dict(r)
            for r in self.templates.values()
            if include_archived or r.get("archived_at") is None
        ]
        rows.sort(key=lambda r: (r["created_at"], str(r["id"])))  # type: ignore[index]
        return rows

    def get_template(self, template_id: str) -> dict[str, object] | None:
        row = self.templates.get(template_id)
        return dict(row) if row else None

    def update_template(
        self,
        template_id: str,
        *,
        name: str | None = None,
        archived_at: datetime | None = None,
        unarchive: bool = False,
    ) -> dict[str, object] | None:
        row = self.templates.get(template_id)
        if row is None:
            return None
        if name is not None:
            row["name"] = name
        if archived_at is not None:
            row["archived_at"] = archived_at
        if unarchive:
            row["archived_at"] = None
        return dict(row)

    # --- versions ---

    def add_version(self, row: dict[str, object]) -> dict[str, object]:
        self.versions[str(row["id"])] = dict(row)
        self.items.setdefault(str(row["id"]), [])
        return dict(row)

    def list_versions(self, template_id: str) -> list[dict[str, object]]:
        rows = [dict(r) for r in self.versions.values() if r["template_id"] == template_id]
        rows.sort(key=lambda r: int(r["version"]), reverse=True)  # type: ignore[call-overload]
        return rows

    def get_version(self, version_id: str) -> dict[str, object] | None:
        row = self.versions.get(version_id)
        return dict(row) if row else None

    def latest_version(self, template_id: str) -> dict[str, object] | None:
        rows = self.list_versions(template_id)
        return rows[0] if rows else None

    def mark_published(
        self, version_id: str, published_at: datetime, published_by: str
    ) -> dict[str, object] | None:
        row = self.versions.get(version_id)
        if row is None:
            return None
        row["published_at"] = published_at
        row["published_by"] = published_by
        return dict(row)

    # --- items ---

    def list_items(self, version_id: str) -> list[dict[str, object]]:
        rows = [dict(r) for r in self.items.get(version_id, [])]
        rows.sort(key=lambda r: int(r["position"]))  # type: ignore[call-overload]
        return rows

    def replace_items(
        self, version_id: str, rows: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        self.items[version_id] = [dict(r) for r in rows]
        return self.list_items(version_id)

    def set_item_config(self, item_id: str, config: dict[str, object]) -> dict[str, object] | None:
        for rows in self.items.values():
            for row in rows:
                if str(row["id"]) == item_id:
                    row["config"] = dict(config)
                    return dict(row)
        return None
