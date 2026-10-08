# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The chart's history fields, grouped as the client page and a note show them.

Each key is the key of the matching field in the psychiatric evaluation
template, so an evaluation's field and the chart field it describes have the
same name. Storage is one row per patient per key, so a new field is a new
entry here, not a migration.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HistoryField:
    key: str
    label: str


@dataclass(frozen=True)
class HistoryGroup:
    key: str
    label: str
    fields: tuple[HistoryField, ...]


SUBSTANCE_USE = "substance_use"

HISTORY_GROUPS: tuple[HistoryGroup, ...] = (
    HistoryGroup(
        "psychiatric_history",
        "Psychiatric history",
        (
            HistoryField("prior_diagnoses", "Earlier diagnoses"),
            HistoryField("psychotherapy_history", "Psychotherapy"),
            HistoryField("medication_trials", "Psychiatric medications tried"),
            HistoryField("hospitalizations", "Hospitalizations and programs"),
            HistoryField("past_self_harm", "Past suicide attempts, self-harm or harm to others"),
            HistoryField("legal_custody", "Legal or custody involvement"),
        ),
    ),
    HistoryGroup(
        "trauma_history", "Trauma history", (HistoryField("trauma_history", "Trauma history"),)
    ),
    HistoryGroup(
        "social_history",
        "Social history and supports",
        (
            HistoryField("living_situation", "Living situation"),
            HistoryField("relationships", "Relationships"),
            HistoryField("work_school", "Work or school"),
            HistoryField("supports", "Supports"),
            HistoryField("cultural_considerations", "Cultural considerations"),
        ),
    ),
    HistoryGroup(
        "medical_history", "Medical history", (HistoryField("medical_history", "Medical history"),)
    ),
    HistoryGroup(
        "family_history",
        "Family history",
        (
            HistoryField("family_psychiatric", "Psychiatric"),
            HistoryField("family_medical", "Medical"),
            HistoryField("family_treatment_response", "Medications that helped relatives"),
        ),
    ),
    # What the client uses, as stated. A visit's screen is the note's, not the chart's.
    HistoryGroup(
        SUBSTANCE_USE,
        "Substance use",
        (
            HistoryField("alcohol", "Alcohol"),
            HistoryField("cannabis", "Cannabis"),
            HistoryField("stimulants", "Stimulants"),
            HistoryField("cocaine", "Cocaine"),
            HistoryField("opioids", "Opioids"),
            HistoryField("benzodiazepines", "Benzodiazepines"),
            HistoryField("tobacco_nicotine", "Tobacco / nicotine"),
            HistoryField("other_substances", "Other substances"),
        ),
    ),
)

#: Every field key, in the order the chart lists them.
HISTORY_KEYS: tuple[str, ...] = tuple(f.key for g in HISTORY_GROUPS for f in g.fields)

#: The substance-use baseline keys, which a note screens with fields of the same name.
SUBSTANCE_KEYS: tuple[str, ...] = next(
    tuple(f.key for f in g.fields) for g in HISTORY_GROUPS if g.key == SUBSTANCE_USE
)

_BY_KEY = {f.key: (g, f) for g in HISTORY_GROUPS for f in g.fields}


def is_history_key(key: str) -> bool:
    return key in _BY_KEY


def field_label(key: str) -> str:
    return _BY_KEY[key][1].label


def group_of(key: str) -> HistoryGroup:
    return _BY_KEY[key][0]
