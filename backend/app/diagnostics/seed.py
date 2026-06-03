# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Idempotent seed of the bundled diagnostic reference data.

Upserts the bundled ICD-10-CM catalog (:mod:`app.diagnostics.catalog`) and the
diagnostic definitions (:mod:`app.diagnostics.baseline`) into the platform-
schema tables. Runs at deploy-time bootstrap (alembic ``env.py``, alongside the
platform ``create_all``). Safe to run repeatedly.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from ..db.platform_models import DiagnosticDefinitionRow
from ..utcnow import utc_now
from .baseline import BASELINE_DEFINITIONS
from .catalog import load_icd10_catalog

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def seed_diagnostic_reference_data(session: Session) -> None:
    """Upsert the ICD-10-CM catalog and diagnostic definitions. Idempotent."""
    load_icd10_catalog(session)

    for def_entry in BASELINE_DEFINITIONS:
        def_row = (
            session.query(DiagnosticDefinitionRow)
            .filter_by(code=def_entry["code"], version=def_entry["version"])
            .one_or_none()
        )
        if def_row is None:
            session.add(
                DiagnosticDefinitionRow(
                    id=str(uuid.uuid4()),
                    code=def_entry["code"],
                    version=def_entry["version"],
                    display_name=def_entry["display_name"],
                    evaluator_type=def_entry["evaluator_type"],
                    params=def_entry["params"],
                    suggested_icd10=def_entry.get("suggested_icd10"),
                    active=True,
                    created_at=utc_now(),
                )
            )
        else:
            def_row.display_name = def_entry["display_name"]
            def_row.evaluator_type = def_entry["evaluator_type"]
            def_row.params = def_entry["params"]
            def_row.suggested_icd10 = def_entry.get("suggested_icd10")
            def_row.active = True

    session.flush()
