"""Verification signal implementations for the hybrid pipeline."""

from app.services.signals.embedding_sim import EmbeddingSimilaritySignal
from app.services.signals.entity_consistency import EntityConsistencySignal
from app.services.signals.hedging import HedgingSignal
from app.services.signals.minicheck_signal import MiniCheckSignal
from app.services.signals.negation import NegationSignal
from app.services.signals.temporal import TemporalConsistencySignal
from app.services.signals.token_overlap import TokenOverlapSignal

MINICHECK_AVAILABLE = True
try:
    import minicheck  # type: ignore[import-not-found]  # noqa: F401
except ImportError:
    MINICHECK_AVAILABLE = False

__all__ = [
    "MINICHECK_AVAILABLE",
    "EmbeddingSimilaritySignal",
    "EntityConsistencySignal",
    "HedgingSignal",
    "MiniCheckSignal",
    "NegationSignal",
    "TemporalConsistencySignal",
    "TokenOverlapSignal",
]
