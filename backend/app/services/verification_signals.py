# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Signal protocol for the hybrid verification pipeline.

Each verification signal implements the VerificationSignal ABC and returns
a SignalResult with a verdict (PASS/FAIL/UNCERTAIN), confidence score,
and human-readable detail for debugging.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class SignalVerdict(Enum):
    """Outcome of a single verification signal."""

    PASS = "pass"  # noqa: S105 - enum value, not a password
    FAIL = "fail"
    UNCERTAIN = "uncertain"


@dataclass
class SignalResult:
    """Result from a single verification signal."""

    verdict: SignalVerdict
    confidence: float
    signal_name: str
    detail: str = ""


@dataclass
class SignalContext:
    """Shared context passed through the signal chain."""

    claim_key: str
    attributed_segment_ids: list[int] = field(default_factory=list)
    candidate_segment_ids: list[int] = field(default_factory=list)
    embedding_similarity: float = 0.0
    all_segment_texts: list[str] = field(default_factory=list)


class VerificationSignal(ABC):
    """Abstract interface for a verification signal."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique signal identifier."""
        ...

    @abstractmethod
    def check(
        self,
        claim_text: str,
        segment_text: str,
        context: SignalContext,
    ) -> SignalResult:
        """Evaluate a single claim-segment pair."""
        ...
