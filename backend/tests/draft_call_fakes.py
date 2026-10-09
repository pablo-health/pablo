# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A gateway that answers a draft's calls by the schema each one sends.

The main draft, the turn labels and the psychotherapy call run side by side,
so the order their calls arrive in is not fixed and a queue of replies cannot
say which reply goes to which. This fake picks by schema: a labelling call
(its schema has ``runs``), the psychotherapy call (titled
:data:`.psychotherapy_section_call.SCHEMA_TITLE`), and everything else, which
is the main draft.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from app.services.psychotherapy_section_call import SCHEMA_TITLE as PSYCHOTHERAPY_TITLE
from app.services.structured_llm_gateway import StructuredCompletion, StructuredLLMGateway


def is_labels(call: dict[str, Any]) -> bool:
    return "runs" in call["response_schema"].get("properties", {})


def is_psychotherapy(call: dict[str, Any]) -> bool:
    return bool(call["response_schema"].get("title") == PSYCHOTHERAPY_TITLE)


@dataclass
class DraftCallsGateway(StructuredLLMGateway):
    """``draft`` answers the main draft; ``labels`` the labelling call, or is raised;
    ``therapy`` the psychotherapy call (an empty block when ``None``)."""

    draft: dict[str, Any]
    labels: dict[str, Any] | Exception = field(
        default_factory=lambda: RuntimeError("no labels scripted")
    )
    therapy: dict[str, Any] | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def complete_structured(self, **kwargs: Any) -> StructuredCompletion:
        with self.lock:
            self.calls.append(kwargs)
        if is_labels(kwargs):
            if isinstance(self.labels, Exception):
                raise self.labels
            return StructuredCompletion(data=self.labels)
        if is_psychotherapy(kwargs):
            return StructuredCompletion(data=self.therapy or {})
        return StructuredCompletion(data=self.draft)

    def main_call(self) -> dict[str, Any]:
        return next(c for c in self.calls if not is_labels(c) and not is_psychotherapy(c))

    def labels_call(self) -> dict[str, Any]:
        return next(c for c in self.calls if is_labels(c))
