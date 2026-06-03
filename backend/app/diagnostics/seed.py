# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Idempotent seed of the bundled diagnostic reference data.

Upserts the baseline ICD-10-CM codes and diagnostic definitions
(:mod:`app.diagnostics.baseline`) into the platform-schema tables. Runs at
deploy-time bootstrap (alembic ``env.py``, alongside the platform
``create_all``). Safe to run repeatedly.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from ..db.platform_models import DiagnosticDefinitionRow, Icd10CodeRow
from ..utcnow import utc_now
from .baseline import BASELINE_DEFINITIONS, BASELINE_ICD10_CODES

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def seed_diagnostic_reference_data(session: Session) -> None:
    """Upsert baseline ICD-10-CM codes and definitions. Idempotent."""
    for code_entry in BASELINE_ICD10_CODES:
        code_row = session.get(Icd10CodeRow, code_entry["code"])
        if code_row is None:
            session.add(
                Icd10CodeRow(
                    code=code_entry["code"],
                    description=code_entry["description"],
                    billable=True,
                    category=code_entry.get("category"),
                )
            )
        else:
            code_row.description = code_entry["description"]
            code_row.category = code_entry.get("category")

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
