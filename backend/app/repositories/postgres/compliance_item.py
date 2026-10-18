# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The compliance-item names, kept for code that still imports them.

Compliance items are tasks with ``category = 'compliance'`` now; see
:mod:`app.repositories.postgres.task`. ``ComplianceItem`` is the same
dataclass as ``Task`` and the repository lists compliance tasks only.
"""

from __future__ import annotations

from .task import PostgresComplianceItemRepository, Task

ComplianceItem = Task

__all__ = ["ComplianceItem", "PostgresComplianceItemRepository"]
