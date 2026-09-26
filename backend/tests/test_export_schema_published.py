# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The published export schema stays the one the export writes.

docs/reference/export-schema.json is what someone reads to build against an
export archive. A field added to the models without regenerating it would
publish a shape the archive no longer has.
"""

from __future__ import annotations

import json

from app.models.export import SCHEMA_VERSION, PatientExportDocument
from scripts.regen_export_schema import SCHEMA_PATH, render


class TestPublishedExportSchema:
    def test_the_committed_schema_matches_the_models(self) -> None:
        assert SCHEMA_PATH.read_text(encoding="utf-8") == render(), (
            "docs/reference/export-schema.json is out of date with app/models/export.py. "
            "Regenerate it with `make export-schema` "
            "(poetry run python backend/scripts/regen_export_schema.py) and commit the result."
        )

    def test_the_committed_schema_is_the_one_the_archive_ships(self) -> None:
        committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        assert committed == PatientExportDocument.model_json_schema()
        assert committed["properties"]["schema_version"]["const"] == SCHEMA_VERSION
