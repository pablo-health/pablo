#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Regenerate docs/reference/export-schema.json from the export models.

The committed file is the published contract for ``patient.json`` in an export
archive, so a script, an agent or another system can read the shape without
running Pablo. Run after changing :mod:`app.models.export` and commit the
result; ``test_export_schema_published.py`` fails if it is stale.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.export import PatientExportDocument

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "reference" / "export-schema.json"


def render() -> str:
    return json.dumps(PatientExportDocument.model_json_schema(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    SCHEMA_PATH.write_text(render(), encoding="utf-8")
    print(f"Wrote {SCHEMA_PATH}")


if __name__ == "__main__":
    main()
