# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Rewrite docs/reference/caqh-intake-field-map.md from the question set.

Run after changing ``app/credentialing/intake.py``; the unit suite fails if the
committed file and the question set disagree.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.credentialing.field_map import FIELD_MAP_PATH, render

    FIELD_MAP_PATH.write_text(render(), encoding="utf-8")
    print(f"Wrote {FIELD_MAP_PATH} ({FIELD_MAP_PATH.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
