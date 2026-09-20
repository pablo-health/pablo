# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Instrument registry for clinical instruments, and who may use them.

New instruments are data, not schema: add an :class:`InstrumentDefinition`
entry to ``INSTRUMENT_REGISTRY`` and the rest of the stack picks it up
automatically — no migration, no new column.

Scored instruments
------------------
- ``phq9``  — Patient Health Questionnaire, 9-item depression screen
- ``gad7``  — Generalized Anxiety Disorder, 7-item anxiety screen
- ``dire``  — Diagnosis, Intractability, Risk, Efficacy; 7-factor clinician
  rating of suitability for long-term opioid therapy (Belgrade 2006)

Scoring shapes that don't fit the uniform per-item scale used here — e.g. an
instrument whose items carry distinct weights, or weights that depend on a
respondent attribute — are intentionally *not* shoehorned in; they need a
weighted-scoring extension (``compute_total`` is a plain sum today). ``dire``
fits the uniform shape (7 factors, each 1-3) and is added as data only.

Rights, and why they are on the registry
----------------------------------------
Not every instrument a practice uses may be reproduced by the software that
asks it. :attr:`InstrumentDefinition.rights` says which of three kinds each
one is, and every other rule in the codebase reads that field rather than
keeping a list of its own:

``public_domain``
    Reproducible by anybody. It is offered everywhere, with no gate.

``attestation_required``
    The wording may be reproduced, but the scale's USE is restricted — free
    for clinical work and licensed for something else, or non-commercial
    only. A practice records that it holds the applicable permission before
    the instrument is offered in its form builder. See
    ``app.services.instrument_license_service``.

``never_ship``
    The form is a sold product. The entry carries the code, the name and the
    item count so a practice can recognise it, and never the item wording. A
    practice that is licensed to use one attaches its own copy as a document
    and scores it outside Pablo.

An entry with no severity bands is one this engine does not score
(:attr:`InstrumentDefinition.is_scored`). Every ``never_ship`` entry is such
an entry, and so is any instrument whose wording has not been added yet: the
registry is then a catalogue of what a practice may be told about, not a
claim that Pablo can compute anything from it.

``rights_note`` is one line a clinician reads in the settings screen, and
``publisher_url`` is where to go and check it. The note is unset for nothing
and the URL is unset where the instrument has no stable publisher page, in
which case the note carries the citation instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: What a deployment is allowed to do with an instrument's wording. See the
#: module docstring; the three values are the whole vocabulary.
InstrumentRights = Literal["public_domain", "attestation_required", "never_ship"]


@dataclass(frozen=True)
class SeverityBand:
    """Maps a score range [low, high] to a human-readable label."""

    low: int
    high: int
    label: str


@dataclass(frozen=True)
class InstrumentDefinition:
    """Describes a clinical instrument, and what may be done with it.

    Parameters
    ----------
    code:
        Short, lowercase identifier used in the ``instrument`` column
        (e.g. ``'phq9'``).
    display_name:
        Human-readable label for UI / reports.
    rights:
        Which of the three kinds this instrument is. See the module
        docstring; nothing in the codebase keeps a second list.
    rights_note:
        One line a clinician reads before attesting, saying what the
        restriction actually is.
    publisher_url:
        Where to go and read the publisher's own terms, or ``None`` where
        the instrument has no stable publisher page and ``rights_note``
        carries the citation instead.
    item_count:
        Expected number of items (determines valid item keys 1..item_count).
    item_min:
        Minimum value for a single item (inclusive).
    item_max:
        Maximum value for a single item (inclusive).
    severity_bands:
        Ordered severity bands covering the full score range.  Must cover
        ``[0, item_count * item_max]`` without gaps or overlaps. Empty means
        this engine does not score the instrument at all — see
        :attr:`is_scored`.
    """

    code: str
    display_name: str
    rights: InstrumentRights
    rights_note: str
    publisher_url: str | None
    item_count: int
    item_min: int = 0
    item_max: int = 0
    severity_bands: tuple[SeverityBand, ...] = ()

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

    @property
    def is_scored(self) -> bool:
        """Whether this engine computes anything from the instrument.

        The severity bands are the test rather than a flag of its own: an
        instrument with no bands has no range to place a total in, which is
        the same thing as not being scored here. A catalogue entry — one
        carrying the name and the item count so a practice can recognise
        the instrument — answers ``False``.
        """
        return bool(self.severity_bands)


# ---------------------------------------------------------------------------
# Registry entries
# ---------------------------------------------------------------------------

_PFIZER_NOTE = (
    "Pfizer funded its development and requires no permission to reproduce, "
    "translate or display it."
)
_PHQ_SCREENERS = "https://www.phqscreeners.com"

_PHQ9 = InstrumentDefinition(
    code="phq9",
    display_name="PHQ-9",
    rights="public_domain",
    rights_note=_PFIZER_NOTE,
    publisher_url=_PHQ_SCREENERS,
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
    rights="public_domain",
    rights_note=_PFIZER_NOTE,
    publisher_url=_PHQ_SCREENERS,
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
    rights="public_domain",
    rights_note=(
        "Published in The Journal of Pain (Belgrade, Schamber and Lindgren, 2006); "
        "free to reproduce with the source cited."
    ),
    publisher_url=None,
    item_count=7,
    item_min=1,
    item_max=3,
    severity_bands=(
        SeverityBand(low=7, high=13, label="not a suitable candidate"),
        SeverityBand(low=14, high=21, label="suitable candidate"),
    ),
)

# ---------------------------------------------------------------------------
# Catalogue entries — instruments a practice may be told about
# ---------------------------------------------------------------------------
#
# None of the entries below carries item wording or severity bands, so none of
# them is scored here (``is_scored`` is False) and none can be put on a form.
# They are in the registry because a practice has to be able to see what the
# restriction is and record its permission, and because the rights value is
# the thing every gate reads. Adding the wording of an ``attestation_required``
# instrument later is a separate change: an entry gains its items, its scale
# and its bands, and the attestation a practice already recorded is what lets
# it be offered.

_CSSRS = InstrumentDefinition(
    code="cssrs",
    display_name="C-SSRS",
    rights="attestation_required",
    rights_note=(
        "Free for clinical and community use; a license applies to pharmaceutical-funded research."
    ),
    publisher_url="https://cssrs.columbia.edu",
    item_count=6,
)

_EPDS = InstrumentDefinition(
    code="epds",
    display_name="EPDS",
    rights="attestation_required",
    rights_note=(
        "A clinician may copy it in full with Cox, Holden and Sagovsky (1987) cited; "
        "the Royal College of Psychiatrists licenses any wider distribution."
    ),
    publisher_url="https://www.rcpsych.ac.uk",
    item_count=10,
)

_DAST10 = InstrumentDefinition(
    code="dast10",
    display_name="DAST-10",
    rights="attestation_required",
    rights_note=(
        "Free for clinical, research and training use with Harvey Skinner credited; "
        "commercial use is licensed by the Centre for Addiction and Mental Health."
    ),
    publisher_url="https://www.camh.ca",
    item_count=10,
)

_ASRS = InstrumentDefinition(
    code="asrs",
    display_name="ASRS v1.1",
    rights="attestation_required",
    rights_note=(
        "Free to use; the World Health Organization holds the copyright, and "
        "reproducing or translating it is permissioned through Harvard Medical School."
    ),
    publisher_url="https://www.hcp.med.harvard.edu/ncs/asrs.php",
    item_count=18,
)

_CAGE = InstrumentDefinition(
    code="cage",
    display_name="CAGE",
    rights="attestation_required",
    rights_note=(
        "Free to use with Ewing (JAMA, 1984) cited; any profit-making use is "
        "negotiated with the copyright holder."
    ),
    publisher_url=None,
    item_count=4,
)

_MDQ = InstrumentDefinition(
    code="mdq",
    display_name="MDQ",
    rights="attestation_required",
    rights_note=(
        "Free for clinical use with Hirschfeld et al. (American Journal of "
        "Psychiatry, 2000) cited; republishing it is permissioned."
    ),
    publisher_url=None,
    item_count=13,
)

_PEARSON = "https://www.pearsonassessments.com"
_PEARSON_NOTE = "Sold by Pearson under a per-use license. Attach your own licensed copy."

_BDI2 = InstrumentDefinition(
    code="bdi2",
    display_name="BDI-II",
    rights="never_ship",
    rights_note=_PEARSON_NOTE,
    publisher_url=_PEARSON,
    item_count=21,
)

_BAI = InstrumentDefinition(
    code="bai",
    display_name="BAI",
    rights="never_ship",
    rights_note=_PEARSON_NOTE,
    publisher_url=_PEARSON,
    item_count=21,
)

_OQ45 = InstrumentDefinition(
    code="oq45",
    display_name="OQ-45.2",
    rights="never_ship",
    rights_note=(
        "Sold by OQ Measures under a per-clinician license. Attach your own licensed copy."
    ),
    publisher_url="https://www.oqmeasures.com",
    item_count=45,
)

_BETTER_OUTCOMES = "https://betteroutcomesnow.com"
_PCOMS_NOTE = "Licensed per clinician by Better Outcomes Now. Attach your own licensed copy."

_ORS = InstrumentDefinition(
    code="ors",
    display_name="ORS",
    rights="never_ship",
    rights_note=_PCOMS_NOTE,
    publisher_url=_BETTER_OUTCOMES,
    item_count=4,
)

_SRS = InstrumentDefinition(
    code="srs",
    display_name="SRS",
    rights="never_ship",
    rights_note=_PCOMS_NOTE,
    publisher_url=_BETTER_OUTCOMES,
    item_count=4,
)


# Registry keyed by instrument code.  Extend by adding entries here.
INSTRUMENT_REGISTRY: dict[str, InstrumentDefinition] = {
    _PHQ9.code: _PHQ9,
    _GAD7.code: _GAD7,
    _DIRE.code: _DIRE,
    _CSSRS.code: _CSSRS,
    _EPDS.code: _EPDS,
    _DAST10.code: _DAST10,
    _ASRS.code: _ASRS,
    _CAGE.code: _CAGE,
    _MDQ.code: _MDQ,
    _BDI2.code: _BDI2,
    _BAI.code: _BAI,
    _OQ45.code: _OQ45,
    _ORS.code: _ORS,
    _SRS.code: _SRS,
}


def instruments_with_rights(rights: InstrumentRights) -> frozenset[str]:
    """Every registered code of one rights kind."""
    return frozenset(code for code, defn in INSTRUMENT_REGISTRY.items() if defn.rights == rights)


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
    - the instrument is a catalogue entry this engine does not score
    - any key is not in ``defn.valid_keys``
    - any value is not an integer in ``[defn.item_min, defn.item_max]``

    The first check is what keeps a catalogue entry from looking scoreable.
    Its item range is empty, so every answer would be out of range anyway
    and the caller would be told the wrong thing about why.
    """
    if not defn.is_scored:
        raise InstrumentValidationError(
            f"{defn.code!r} is not an instrument Pablo scores. Record the total "
            f"from your own copy of the form."
        )
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
