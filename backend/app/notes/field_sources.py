# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where a note field's content comes from when code writes it, not the model.

A field with a ``source`` is printed from the chart, or from the values
entered for the visit, by :mod:`app.notes.chart_fields`; the model is only
asked what the visit said about it. The source names what is printed:

- ``allergies``: the allergy record.
- ``medications``: the current medication list, a line per medication.
- ``problems``: the problem list, as diagnoses with their codes.
- ``place_of_service``: the place-of-service attestation, from the entered
  place of service and locations.
- any chart-history key (the eight substance-use keys included): that
  history field's text. A substance-use key also carries this visit's screen.

The source is part of the field's definition, so a field a practice adds to
a base type can name one too, and nothing decides it by the field's key.
"""

from __future__ import annotations

from ..chart_history.fields import HISTORY_KEYS, SUBSTANCE_KEYS

ALLERGIES = "allergies"
MEDICATIONS = "medications"
PROBLEMS = "problems"
PLACE_OF_SERVICE = "place_of_service"

FIELD_SOURCES: frozenset[str] = frozenset(
    {ALLERGIES, MEDICATIONS, PROBLEMS, PLACE_OF_SERVICE, *HISTORY_KEYS}
)

_KIND_FOR_SOURCE = {MEDICATIONS: "list", PROBLEMS: "diagnoses"}


def kind_for_source(source: str) -> str:
    """The field kind a source's content takes: a list, diagnoses, or text."""
    return _KIND_FOR_SOURCE.get(source, "text")


def is_substance(source: str | None) -> bool:
    return source is not None and source in SUBSTANCE_KEYS


def is_history(source: str | None) -> bool:
    return source is not None and source in HISTORY_KEYS
