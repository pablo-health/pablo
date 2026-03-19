# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""MiniCheck fact-verification escalation signal.

Handles the ~10-15% of claim-segment pairs where all simpler signals returned
UNCERTAIN. MiniCheck-RoBERTa-Large is a purpose-built fact-verification model
that outperforms general NLI on grounded claim verification.

The model is loaded lazily on first use and runs locally (self-hosted, no
external API calls). PHI never leaves our infrastructure.
"""

from __future__ import annotations

from typing import Any

from ..verification_signals import (
    SignalContext,
    SignalResult,
    SignalVerdict,
    VerificationSignal,
)

_PASS_PROB_THRESHOLD = 0.75
_FAIL_PROB_THRESHOLD = 0.25
_MAX_PASS_CONFIDENCE = 0.92


class MiniCheckSignal(VerificationSignal):
    """MiniCheck-RoBERTa-Large for fact verification escalation.

    MiniCheck expects (document, claim) format where document is the
    transcript segment and claim is the SOAP note sentence.
    """

    def __init__(self, model_path: str = "roberta-large") -> None:
        self._model_path = model_path
        self._model: Any = None

    def _get_model(self) -> Any:
        """Lazily load the MiniCheck model on first use."""
        if self._model is None:
            from minicheck.minicheck import MiniCheck  # type: ignore[import-not-found]

            self._model = MiniCheck(
                model_name=self._model_path, enable_prefix_caching=False,
            )
        return self._model

    @property
    def name(self) -> str:
        return "minicheck"

    def check(
        self,
        claim_text: str,
        segment_text: str,
        _context: SignalContext,
    ) -> SignalResult:
        model = self._get_model()
        # MiniCheck expects (document, claim) format
        pred_label, raw_prob, _, _ = model.score(
            docs=[segment_text], claims=[claim_text],
        )

        prob = float(raw_prob[0])
        label = int(pred_label[0])

        if label == 1 and prob >= _PASS_PROB_THRESHOLD:
            return SignalResult(
                verdict=SignalVerdict.PASS,
                confidence=min(_MAX_PASS_CONFIDENCE, prob),
                signal_name=self.name,
                detail=f"MiniCheck supported: {prob:.3f}",
            )

        if label == 0 and prob <= _FAIL_PROB_THRESHOLD:
            return SignalResult(
                verdict=SignalVerdict.FAIL,
                confidence=prob * 0.3,
                signal_name=self.name,
                detail=f"MiniCheck unsupported: {prob:.3f}",
            )

        return SignalResult(
            verdict=SignalVerdict.UNCERTAIN,
            confidence=prob * 0.6,
            signal_name=self.name,
            detail=f"MiniCheck ambiguous: {prob:.3f}",
        )
