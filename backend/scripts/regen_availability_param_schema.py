#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Regenerate the availability-rule params schema the frontend is pinned to.

The frontend keeps a hand-written ``validate()`` in AvailabilitySettings.tsx
because it has to tell the therapist what's wrong before the request goes
out. That used to be a third hand-kept copy of the param shapes. It now
reads this file instead, and its test fails when the two disagree, so the
backend models stay the one authority.

Run after changing :mod:`app.models.availability_rule_params` and commit
the result; ``test_availability_rule_params.py`` fails if it's stale.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.availability_rule_params import rule_params_json_schema

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "types"
    / "availabilityRuleParams.schema.json"
)


def render() -> str:
    return json.dumps(rule_params_json_schema(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    SCHEMA_PATH.write_text(render())
    print(f"Wrote {SCHEMA_PATH}")


if __name__ == "__main__":
    main()
