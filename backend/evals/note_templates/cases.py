# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Cases for the note-template eval.

Each case drafts one of a starting template's own sample visits, the same
transcripts Settings offers under "Try it", so the eval grades exactly what
a clinician sees there. The samples are synthetic: written for the
template, about no one. The values entered before the visit (locations,
diagnoses) are invented here, in the same spirit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

from app.notes.practice_types import PracticeNoteTypeSpec

TEMPLATES_DIR = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "components"
    / "settings"
    / "noteTypes"
    / "templates"
)


@dataclass(frozen=True)
class Diagnosis:
    """A diagnosis the clinician entered or named: its code, if one was given,
    and words any one of which names it."""

    code: str | None
    terms: tuple[str, ...]


@dataclass(frozen=True)
class Expected:
    """What the visit and the entered values support, and nothing more.

    ``codes``, ``times`` and ``minutes`` are what the clinician dictated;
    the draft may carry these and no others. ``pdmp_findings`` is set when
    the clinician said the prescription monitoring program was checked:
    words of the finding the PDMP line must carry. ``telehealth`` holds the
    entered client and provider locations when the visit was by telehealth.
    """

    therapy: bool
    codes: tuple[str, ...] = ()
    times: tuple[str, ...] = ()
    minutes: tuple[str, ...] = ()
    pdmp_findings: tuple[str, ...] | None = None
    telehealth: tuple[str, str] | None = None
    substances_asked: tuple[str, ...] = ()
    substances_not_asked: tuple[str, ...] = ()
    diagnoses: tuple[Diagnosis, ...] = ()


@dataclass(frozen=True)
class TemplateCase:
    name: str
    template: str
    """The starting template's file name, without ``.json``."""
    sample: str
    """The id of one of the template's sample visits."""
    session_date: date
    expected: Expected
    inputs: dict[str, str] = field(default_factory=dict)

    @property
    def spec(self) -> PracticeNoteTypeSpec:
        return PracticeNoteTypeSpec.model_validate(_template(self.template)["spec"])

    @property
    def transcript(self) -> str:
        samples = _template(self.template)["samples"]
        return str(next(s["transcript"] for s in samples if s["id"] == self.sample))


@cache
def _template(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((TEMPLATES_DIR / f"{name}.json").read_text())
    return data


FOLLOW_UP_WITH_THERAPY = TemplateCase(
    name="follow-up-with-therapy",
    template="psychiatric_follow_up",
    sample="with_therapy",
    session_date=date(2026, 3, 12),
    inputs={
        "place_of_service": "Telehealth",
        "client_location": "Client's home in Faketown, AA",
        "provider_location": "Clinic office at 123 Test St, Faketown, AA",
        "diagnoses": (
            "F41.1 Generalized anxiety disorder; "
            "F90.0 Attention-deficit hyperactivity disorder, predominantly inattentive type"
        ),
    },
    expected=Expected(
        therapy=True,
        codes=("99214", "90836"),
        times=("10:14", "10:55"),
        minutes=("41",),
        pdmp_findings=("early fill", "other prescriber"),
        telehealth=(
            "Client's home in Faketown, AA",
            "Clinic office at 123 Test St, Faketown, AA",
        ),
        substances_asked=("alcohol", "tobacco_nicotine", "cannabis", "other_substances"),
        diagnoses=(
            Diagnosis("F41.1", ("anxiety",)),
            Diagnosis("F90.0", ("attention", "adhd", "hyperactivity")),
        ),
    ),
)

FOLLOW_UP_MEDICATION_ONLY = TemplateCase(
    name="follow-up-medication-only",
    template="psychiatric_follow_up",
    sample="medication_only",
    session_date=date(2026, 3, 13),
    inputs={"place_of_service": "In office"},
    expected=Expected(
        therapy=False,
        # Asked "Any alcohol or anything else?" and answered only about
        # alcohol: tobacco and cannabis never came up. "Anything else" is
        # left ungraded, since it was asked but not answered.
        substances_asked=("alcohol",),
        substances_not_asked=("tobacco_nicotine", "cannabis"),
        diagnoses=(Diagnosis(None, ("depress",)),),
    ),
)

ALL_CASES: tuple[TemplateCase, ...] = (FOLLOW_UP_WITH_THERAPY, FOLLOW_UP_MEDICATION_ONLY)
