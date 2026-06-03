# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Providers that resolve a definition code to a :class:`DiagnosticDefinition`.

Definitions are data in the platform ``diagnostic_definitions`` table; the
runtime path reads them from there (:class:`DbDefinitionProvider`). A
DB-free :class:`BaselineDefinitionProvider` backed by the bundled content
serves unit tests and any fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from ..db.platform_models import DiagnosticDefinitionRow
from .baseline import BASELINE_DEFINITIONS
from .definitions import DiagnosticDefinition, definition_from_row

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class DefinitionProvider(Protocol):
    """Resolves diagnostic definitions for the service layer."""

    def get(self, code: str, version: int | None = None) -> DiagnosticDefinition | None: ...

    def list_active(self) -> list[DiagnosticDefinition]: ...


def _row_to_mapping(row: DiagnosticDefinitionRow) -> dict[str, Any]:
    """Shape an ORM definition row as the mapping ``definition_from_row`` expects."""
    return {
        "code": row.code,
        "version": row.version,
        "display_name": row.display_name,
        "evaluator_type": row.evaluator_type,
        "suggested_icd10": row.suggested_icd10,
        "params": row.params,
    }


class BaselineDefinitionProvider:
    """In-memory provider over the bundled baseline content (tests/fallback)."""

    def __init__(self, entries: list[dict] | None = None) -> None:
        self._by_code: dict[str, DiagnosticDefinition] = {}
        for entry in entries if entries is not None else BASELINE_DEFINITIONS:
            # Baseline entries are already definition-row shaped.
            self._by_code[entry["code"]] = definition_from_row(entry)

    def get(self, code: str, version: int | None = None) -> DiagnosticDefinition | None:
        defn = self._by_code.get(code)
        if defn is None or (version is not None and defn.version != version):
            return None
        return defn

    def list_active(self) -> list[DiagnosticDefinition]:
        return list(self._by_code.values())


class DbDefinitionProvider:
    """Reads active definitions from the platform ``diagnostic_definitions`` table."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, code: str, version: int | None = None) -> DiagnosticDefinition | None:
        query = self._session.query(DiagnosticDefinitionRow).filter(
            DiagnosticDefinitionRow.code == code,
            DiagnosticDefinitionRow.active.is_(True),
        )
        if version is not None:
            query = query.filter(DiagnosticDefinitionRow.version == version)
        row = query.order_by(DiagnosticDefinitionRow.version.desc()).first()
        if row is None:
            return None
        return definition_from_row(_row_to_mapping(row))

    def list_active(self) -> list[DiagnosticDefinition]:
        rows = (
            self._session.query(DiagnosticDefinitionRow)
            .filter(DiagnosticDefinitionRow.active.is_(True))
            .order_by(DiagnosticDefinitionRow.code.asc(), DiagnosticDefinitionRow.version.desc())
            .all()
        )
        # First (highest) version per code.
        seen: set[str] = set()
        out: list[DiagnosticDefinition] = []
        for row in rows:
            if row.code in seen:
                continue
            seen.add(row.code)
            out.append(definition_from_row(_row_to_mapping(row)))
        return out
