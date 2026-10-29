# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The untyped row shape repositories exchange with their service layer.

Repository ports hand services plain dicts whose keys mirror the table's
columns. ``Any`` values let a mapper feed ``row.get("x")`` straight into the
matching ORM or dataclass field; the column types are enforced by the ORM
model on the other side, not re-declared here.
"""

from __future__ import annotations

from typing import Any

type Row = dict[str, Any]
