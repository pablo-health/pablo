# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Cases for the note-type template eval.

Each case drafts one of a template's own sample visits (the synthetic
transcripts Settings offers under "Try it") and says what a faithful draft of
that visit looks like. The transcripts live in the template files; nothing
here is about a real person.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TEMPLATES = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "components"
    / "settings"
    / "noteTypes"
    / "templates"
)

NOT_COVERED = ("not stated", "not asked")
"""What a field the visit did not cover must say (compared without case or full stop)."""


@dataclass(frozen=True)
class TemplateCase:
    """One template sample, drafted, and what the draft must and must not hold.

    Fields are ``section.field``. Every field not named here must be filled.
    ``not_covered`` fields must say "Not stated." or "Not asked."; ``empty``
    fields must be blank; ``may_be_empty`` fields may be either. Diagnoses
    must come back with exactly ``stated_codes``, each item carrying its code,
    and ``rule_out`` codes marked as such. No value may contain a
    ``forbidden`` string (a code or level the clinician never said).

    A case may bring its own ``transcript`` instead of a template sample.
    ``recorded_call`` drafts it as a two-channel call, so the clinician's
    turns after the client's last line reach the model as the dictated
    addendum. Each ``quoted`` pair is a field and a phrase it must quote.
    With ``fill_unnamed`` off, fields the case does not name are not graded.
    """

    name: str
    template: str
    sample: str
    inputs: dict[str, str]
    not_covered: tuple[str, ...] = ()
    empty: tuple[str, ...] = ()
    may_be_empty: tuple[str, ...] = ()
    diagnoses_field: str | None = None
    stated_codes: frozenset[str] = frozenset()
    rule_out: frozenset[str] = frozenset()
    forbidden: tuple[str, ...] = field(default_factory=tuple)
    transcript: str | None = None
    recorded_call: bool = False
    quoted: tuple[tuple[str, str], ...] = ()
    fill_unnamed: bool = True


def load_template(template: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((TEMPLATES / f"{template}.json").read_text())
    return loaded


def sample_transcript(template: str, sample: str) -> str:
    samples = load_template(template)["samples"]
    return str(next(s["transcript"] for s in samples if s["id"] == sample))


_PSYCHOTHERAPY = tuple(
    f"psychotherapy.{key}"
    for key in ("psychotherapy_time", "modality_interventions", "response", "goal_plan")
)

INTAKE_NEW_CLIENT = TemplateCase(
    name="psychiatric-evaluation-new-client",
    template="psychiatric_evaluation",
    sample="new_client",
    inputs={
        "place_of_service": "Telehealth",
        "client_location": "Home",
        "provider_location": "Office",
        "visit_code": "Psychiatric diagnostic evaluation (90792)",
    },
    not_covered=(
        "psychiatric_history.legal_custody",
        "psychiatric_ros.eating",
        "psychiatric_ros.trauma_responses",
    ),
    empty=_PSYCHOTHERAPY,
    may_be_empty=("treatment_plan.off_label", "prescriptions.pdmp"),
    diagnoses_field="assessment.diagnoses",
    stated_codes=frozenset({"F32.1", "F41.1", "F90.0"}),
    rule_out=frozenset({"F90.0"}),
    # An E/M level, a psychotherapy add-on, or an alcohol use disorder code:
    # none was stated, so any of them would be invented.
    forbidden=("99202", "99203", "99204", "99205", "90833", "90836", "90838", "F10."),
)


# A medication check by video. The client never speaks of risk; the clinician
# states it, and the mental status findings, in the addendum dictated after the
# client has gone. Neither the visit nor the addendum covers self-harm,
# orientation or cognition.
_FOLLOW_UP_WITH_ADDENDUM = "\n".join(
    [
        "[00:00:04] Therapist: Hi, good to see you again. Are you at home today?",
        "[00:00:08] Client: Yes, I'm at home.",
        "[00:00:12] Therapist: How has the sertraline been since we went up to 100?",
        "[00:00:20] Client: Better. I'm sleeping through most nights and the mornings "
        "are less heavy. A little nausea the first week, gone now.",
        "[00:00:41] Therapist: Good. Any missed doses?",
        "[00:00:44] Client: Maybe one, when I travelled.",
        "[00:00:50] Therapist: That's fine. We'll stay at 100 and meet again in six weeks.",
        "[00:00:58] Client: Sounds good. Thank you, see you then.",
        "[00:01:20] Therapist: Addendum for the note. Client denies suicidal ideation, "
        "intent or plan. Overall risk is low.",
        "[00:01:34] Therapist: Mood described as better, affect brighter than last "
        "visit. Continue sertraline 100 milligrams daily.",
    ]
)

FOLLOW_UP_ADDENDUM = TemplateCase(
    name="psychiatric-follow-up-dictated-addendum",
    template="psychiatric_follow_up",
    sample="",
    transcript=_FOLLOW_UP_WITH_ADDENDUM,
    recorded_call=True,
    inputs={"place_of_service": "Telehealth", "client_location": "Home"},
    quoted=(
        ("risk.suicidal_homicidal_ideation", "denies suicidal ideation"),
        ("risk.overall_risk", "risk is low"),
        ("mse.mood_affect", "brighter"),
    ),
    not_covered=("risk.self_harm_violence", "mse.orientation", "mse.cognition"),
    fill_unnamed=False,
)


def all_cases() -> tuple[TemplateCase, ...]:
    return (INTAKE_NEW_CLIENT, FOLLOW_UP_ADDENDUM)
