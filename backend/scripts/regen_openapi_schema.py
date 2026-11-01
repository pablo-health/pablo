#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Regenerate frontend/src/types/api/openapi.json from the backend's routes.

The frontend's API types are generated from this file (``npm run gen:api``),
so a request or response shape the backend changes shows up as a type error
in the screens that use it instead of a mismatch at runtime. Run after
changing a route or its Pydantic models and commit the result;
``test_openapi_schema_published.py`` fails if it is stale.

Some routers mount only when a setting turns them on (the patient portal and
its modules, public booking, launch intent, the chats). The schema is
rendered with every one of them on, in a fresh process with a minimal
environment and no ``.env``, so it describes every route the frontend can
call and comes out the same on every machine.

A response field is marked required whenever the backend always sends it,
defaults included, so the generated types don't make a screen guard against
a field that is never missing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SCHEMA_PATH = BACKEND.parent / "frontend" / "src" / "types" / "api" / "openapi.json"

ALL_ROUTES = {
    "ENABLE_LAUNCH_INTENT": "true",
    "PUBLIC_BOOKING_ENABLED": "true",
    "ENABLE_PATIENT_PORTAL": "true",
    "ENABLE_PATIENT_CHAT": "true",
    "ENABLE_PATIENT_PORTAL_CHAT": "true",
    "PORTAL_MODULES": "intake,messaging,documents,appointments,homework,refills,billing,chat",
}

# A response always carries every field, defaults included (no route drops
# fields with response_model_exclude_*), so the schema says so: this is
# Pydantic's json_schema_serialization_defaults_required, for every model.
# Requests are unchanged; a field with a default stays optional to send.
_DUMP = (
    "import json, sys\n"
    "from pydantic.json_schema import GenerateJsonSchema\n"
    "_required = GenerateJsonSchema.field_is_required\n"
    "def _always_sent(self, field, total):\n"
    "    if self.mode == 'serialization':\n"
    "        return field.get('serialization_exclude_if') is None\n"
    "    return _required(self, field, total)\n"
    "GenerateJsonSchema.field_is_required = _always_sent\n"
    "from app.main import app\n"
    "with open(sys.argv[1], 'w', encoding='utf-8') as out:\n"
    "    json.dump(app.openapi(), out, indent=2, sort_keys=True)\n"
    "    out.write('\\n')\n"
)


def render() -> str:
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(BACKEND), **ALL_ROUTES}
    with tempfile.TemporaryDirectory() as cwd:
        target = Path(cwd) / "openapi.json"
        subprocess.run(
            [sys.executable, "-c", _DUMP, str(target)],
            cwd=cwd,
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        return target.read_text(encoding="utf-8")


def main() -> None:
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(render(), encoding="utf-8")
    print(f"Wrote {SCHEMA_PATH}")


if __name__ == "__main__":
    main()
