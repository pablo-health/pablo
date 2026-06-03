# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Instrument registry for scored clinical instruments.

New instruments are data, not schema: add an :class:`InstrumentDefinition`
entry to ``INSTRUMENT_REGISTRY`` and the rest of the stack picks it up
automatically — no migration, no new column.

Supported instruments
---------------------
- ``phq9``  — Patient Health Questionnaire, 9-item depression screen
- ``gad7``  — Generalized Anxiety Disorder, 7-item anxiety screen
- ``dire``  — Diagnosis, Intractability, Risk, Efficacy; 7-factor clinician
  rating of suitability for long-term opioid therapy (Belgrade 2006)

Scoring shapes that don't fit the uniform per-item scale used here — e.g. an
instrument whose items carry distinct weights, or weights that depend on a
respondent attribute — are intentionally *not* shoehorned in; they need a
weighted-scoring extension (``compute_total`` is a plain sum today). ``dire``
fits the uniform shape (7 factors, each 1-3) and is added as data only.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SeverityBand:
    """Maps a score range [low, high] to a human-readable label."""

    low: int
    high: int
    label: str


@dataclass(frozen=True)
class InstrumentDefinition:
    """Describes a scored clinical instrument.

    Parameters
    ----------
    code:
        Short, lowercase identifier used in the ``instrument`` column
        (e.g. ``'phq9'``).
    item_count:
        Expected number of items (determines valid item keys 1..item_count).
    item_min:
        Minimum value for a single item (inclusive).
    item_max:
        Maximum value for a single item (inclusive).
    severity_bands:
        Ordered severity bands covering the full score range.  Must cover
        ``[0, item_count * item_max]`` without gaps or overlaps.
    display_name:
        Human-readable label for UI / reports.
    """

    code: str
    item_count: int
    item_min: int
    item_max: int
    severity_bands: tuple[SeverityBand, ...]
    display_name: str = ""

    # Derived: valid item keys are str(1) .. str(item_count)
    @property
    def valid_keys(self) -> frozenset[str]:
        return frozenset(str(i) for i in range(1, self.item_count + 1))

    @property
    def max_total(self) -> int:
        return self.item_count * self.item_max

    @property
    def min_total(self) -> int:
        return self.item_count * self.item_min


# ---------------------------------------------------------------------------
# Registry entries
# ---------------------------------------------------------------------------

_PHQ9 = InstrumentDefinition(
    code="phq9",
    display_name="PHQ-9",
    item_count=9,
    item_min=0,
    item_max=3,
    severity_bands=(
        SeverityBand(low=0, high=4, label="minimal"),
        SeverityBand(low=5, high=9, label="mild"),
        SeverityBand(low=10, high=14, label="moderate"),
        SeverityBand(low=15, high=19, label="moderately severe"),
        SeverityBand(low=20, high=27, label="severe"),
    ),
)

_GAD7 = InstrumentDefinition(
    code="gad7",
    display_name="GAD-7",
    item_count=7,
    item_min=0,
    item_max=3,
    severity_bands=(
        SeverityBand(low=0, high=4, label="minimal"),
        SeverityBand(low=5, high=9, label="mild"),
        SeverityBand(low=10, high=14, label="moderate"),
        SeverityBand(low=15, high=21, label="severe"),
    ),
)

# DIRE — Diagnosis, Intractability, Risk, Efficacy (Belgrade 2006). Seven
# clinician-rated factors (Diagnosis, Intractability, the four Risk subscales —
# psychological, chemical health, reliability, social support — and Efficacy),
# each scored 1-3. Higher totals indicate a *more* suitable candidate for
# long-term opioid therapy (opposite valence to the symptom screeners): 7-13
# "not a suitable candidate", 14-21 "suitable candidate".
_DIRE = InstrumentDefinition(
    code="dire",
    display_name="DIRE",
    item_count=7,
    item_min=1,
    item_max=3,
    severity_bands=(
        SeverityBand(low=7, high=13, label="not a suitable candidate"),
        SeverityBand(low=14, high=21, label="suitable candidate"),
    ),
)

# Registry keyed by instrument code.  Extend by adding entries here.
INSTRUMENT_REGISTRY: dict[str, InstrumentDefinition] = {
    _PHQ9.code: _PHQ9,
    _GAD7.code: _GAD7,
    _DIRE.code: _DIRE,
}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class InstrumentValidationError(ValueError):
    """Raised when item_scores or total_score fail instrument constraints."""


def get_instrument(code: str) -> InstrumentDefinition | None:
    """Return the definition for *code*, or ``None`` if unrecognised."""
    return INSTRUMENT_REGISTRY.get(code)


def validate_item_scores(defn: InstrumentDefinition, item_scores: dict[str, int]) -> None:
    """Validate *item_scores* against *defn*.

    Raises :class:`InstrumentValidationError` if:
    - any key is not in ``defn.valid_keys``
    - any value is not an integer in ``[defn.item_min, defn.item_max]``
    """
    for key, value in item_scores.items():
        if key not in defn.valid_keys:
            raise InstrumentValidationError(
                f"Unknown item key {key!r} for instrument {defn.code!r}. "
                f"Valid keys: {sorted(defn.valid_keys)}"
            )
        if not isinstance(value, int):
            raise InstrumentValidationError(
                f"Item {key!r}: value must be an integer, got {type(value).__name__}"
            )
        if not (defn.item_min <= value <= defn.item_max):
            raise InstrumentValidationError(
                f"Item {key!r}: value {value} is out of range [{defn.item_min}, {defn.item_max}]"
            )


def compute_total(
    defn: InstrumentDefinition,  # noqa: ARG001 — reserved for future per-item weighting
    item_scores: dict[str, int],
) -> int:
    """Sum the values in *item_scores* (already validated).

    The caller is responsible for calling :func:`validate_item_scores` first.
    ``defn`` is accepted for API consistency with other helpers and to allow
    future per-instrument item weighting without a signature break.
    """
    return sum(item_scores.values())


def severity_label(defn: InstrumentDefinition, total: int) -> str | None:
    """Return the severity label for *total*, or ``None`` if out of range."""
    for band in defn.severity_bands:
        if band.low <= total <= band.high:
            return band.label
    return None


def is_complete(defn: InstrumentDefinition, item_scores: dict[str, int]) -> bool:
    """Return ``True`` when *item_scores* contains all required item keys."""
    return defn.valid_keys.issubset(item_scores.keys())
