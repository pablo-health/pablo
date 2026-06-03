# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Bundled diagnostic definitions.

The engine reads diagnostic definitions from the platform
``diagnostic_definitions`` table at runtime; this module is the source the seed
(:mod:`app.diagnostics.seed`) upserts. No definitions ship by default — a
deployment supplies its own as data (one row per disorder/version). The
ICD-10-CM codes a definition references come from the bundled catalog
(:mod:`app.diagnostics.catalog`).
"""

from __future__ import annotations

from typing import Any

# No definitions are bundled by default. Populate per deployment (data, not
# code) via the platform ``diagnostic_definitions`` table.
BASELINE_DEFINITIONS: list[dict[str, Any]] = []
