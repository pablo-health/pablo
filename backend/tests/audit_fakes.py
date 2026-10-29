# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A stand-in for ``AuditService`` that remembers what it was asked to record."""

from __future__ import annotations

from typing import Any


class RecordingAudit:
    """Records audit calls without a repository behind them.

    ``entries`` keeps each ``log`` call whole (action plus keyword arguments);
    ``actions`` and ``changes`` are the same calls flattened, and also take the
    patient-principal helper, so a test can read whichever shape suits it.
    """

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self.actions: list[str] = []
        self.changes: list[dict[str, Any]] = []

    def log(self, action: Any, *_args: Any, **kwargs: Any) -> None:
        self.entries.append({"action": action, **kwargs})
        self._record(action, kwargs)

    def log_patient_principal_action(self, action: Any, *_args: Any, **kwargs: Any) -> None:
        self._record(action, kwargs)

    def changes_for(self, action: Any) -> list[dict[str, Any]]:
        return [
            payload
            for name, payload in zip(self.actions, self.changes, strict=True)
            if action.value in name
        ]

    def _record(self, action: Any, kwargs: dict[str, Any]) -> None:
        self.actions.append(str(action))
        self.changes.append(kwargs.get("changes") or {})
