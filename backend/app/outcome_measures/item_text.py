# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Item prompts and response anchors for the self-report screeners.

:mod:`instruments` is the source of truth for *scoring* — how many items an
instrument has, what range an item may take, which severity band a total
falls in. It deliberately carries no wording, because scoring does not need
any. A form does, so the wording lives here.

PHQ-9 and GAD-7 are in the public domain: Pfizer, which funded their
development, requires no permission to reproduce, translate or display
either one. The prompts and the four frequency anchors below are therefore
quoted verbatim rather than paraphrased. An altered item is a different
instrument, and its severity bands stop meaning what the literature says
they mean.

  Kroenke K, Spitzer RL, Williams JBW. The PHQ-9: validity of a brief
  depression severity measure. J Gen Intern Med. 2001;16(9):606-613.

  Spitzer RL, Kroenke K, Williams JBW, Löwe B. A brief measure for assessing
  generalized anxiety disorder: the GAD-7. Arch Intern Med.
  2006;166(10):1092-1097.

The clinician-facing manual-entry form renders the same public-domain text
from ``frontend/src/lib/outcomeMeasures.ts``. The two copies are deliberate:
that one is a form a clinician types into, this one is what the patient-facing
API hands to whoever renders the patient's own form. Change one, change the
other — ``test_outcome_measure_item_text.py`` pins both lists against the
instrument registry, so a count that drifts fails there first.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResponseOption:
    """One answer a respondent may pick, and the value it scores."""

    value: int
    label: str


# The stem both screeners share, shown once above their items.
FREQUENCY_PROMPT = (
    "Over the last 2 weeks, how often have you been bothered by any of the following problems?"
)

FREQUENCY_OPTIONS: tuple[ResponseOption, ...] = (
    ResponseOption(value=0, label="Not at all"),
    ResponseOption(value=1, label="Several days"),
    ResponseOption(value=2, label="More than half the days"),
    ResponseOption(value=3, label="Nearly every day"),
)

# Ordered; index 0 is item key "1".
PHQ9_ITEMS: tuple[str, ...] = (
    "Little interest or pleasure in doing things",
    "Feeling down, depressed, or hopeless",
    "Trouble falling or staying asleep, or sleeping too much",
    "Feeling tired or having little energy",
    "Poor appetite or overeating",
    "Feeling bad about yourself — or that you are a failure or have let yourself or your "
    "family down",
    "Trouble concentrating on things, such as reading the newspaper or watching television",
    "Moving or speaking so slowly that other people could have noticed — or the opposite, being so "
    "fidgety or restless that you have been moving around a lot more than usual",
    "Thoughts that you would be better off dead, or of hurting yourself in some way",
)

GAD7_ITEMS: tuple[str, ...] = (
    "Feeling nervous, anxious, or on edge",
    "Not being able to stop or control worrying",
    "Worrying too much about different things",
    "Trouble relaxing",
    "Being so restless that it is hard to sit still",
    "Becoming easily annoyed or irritable",
    "Feeling afraid, as if something awful might happen",
)

ITEM_TEXT: dict[str, tuple[str, ...]] = {
    "phq9": PHQ9_ITEMS,
    "gad7": GAD7_ITEMS,
}

__all__ = [
    "FREQUENCY_OPTIONS",
    "FREQUENCY_PROMPT",
    "GAD7_ITEMS",
    "ITEM_TEXT",
    "PHQ9_ITEMS",
    "ResponseOption",
]
