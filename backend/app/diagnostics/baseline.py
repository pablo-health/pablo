# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Baseline diagnostic definitions (bundled content).

A small, self-authored set of common diagnoses so the engine is usable out of
the box. The criterion wording is original (expressed from the well-established
clinical facts, not copied from any copyrighted source) and is intended for
clinical review per deployment before clinical use. The ICD-10-CM codes these
definitions reference live in the bundled catalog (:mod:`app.diagnostics.catalog`).

This data is the *source* the seed (:mod:`app.diagnostics.seed`) upserts into
the platform tables; at runtime the engine reads definitions from the database,
so a deployment may add, version, or override them as data.
"""

from __future__ import annotations

from typing import Any

_MDD: dict[str, Any] = {
    "code": "mdd",
    "version": 1,
    "display_name": "Major Depressive Disorder",
    "evaluator_type": "criteria",
    "suggested_icd10": "F32.9",
    "params": {
        "criterion_groups": [
            {
                "key": "A",
                "label": "Core symptoms",
                "min_met": 5,
                "require_cardinal": True,
                "criteria": [
                    {
                        "key": "A1",
                        "label": "Low or depressed mood most of the day, most days",
                        "cardinal": True,
                    },
                    {
                        "key": "A2",
                        "label": "Loss of interest or enjoyment in nearly all activities",
                        "cardinal": True,
                    },
                    {
                        "key": "A3",
                        "label": "Notable change in appetite or weight (not intentional)",
                    },
                    {"key": "A4", "label": "Trouble sleeping or sleeping too much, most days"},
                    {"key": "A5", "label": "Observable restlessness or slowing of movement"},
                    {"key": "A6", "label": "Persistent tiredness or loss of energy"},
                    {"key": "A7", "label": "Feelings of worthlessness or excessive guilt"},
                    {"key": "A8", "label": "Reduced ability to concentrate or make decisions"},
                    {"key": "A9", "label": "Recurring thoughts of death or self-harm"},
                ],
            }
        ],
        "gates": [
            {"key": "duration", "label": "Symptoms present for at least about two weeks"},
            {"key": "impairment", "label": "Symptoms cause meaningful distress or impairment"},
            {
                "key": "not_substance_medical",
                "label": "Not better accounted for by a substance or medical condition",
            },
            {"key": "not_psychotic", "label": "Not better explained by a psychotic disorder"},
            {"key": "no_mania_history", "label": "No history of a manic or hypomanic episode"},
        ],
        "icd10_options": [
            {"code": "F32.9", "label": "MDD, single episode, unspecified"},
            {"code": "F33.9", "label": "MDD, recurrent, unspecified"},
        ],
    },
}

_GAD: dict[str, Any] = {
    "code": "gad",
    "version": 1,
    "display_name": "Generalized Anxiety Disorder",
    "evaluator_type": "criteria",
    "suggested_icd10": "F41.1",
    "params": {
        "criterion_groups": [
            {
                "key": "A",
                "label": "Worry",
                "min_met": 2,
                "criteria": [
                    {
                        "key": "A1",
                        "label": (
                            "Excessive anxiety and worry, more days than not, "
                            "about several things"
                        ),
                    },
                    {"key": "A2", "label": "Finds it hard to control the worry"},
                ],
            },
            {
                "key": "B",
                "label": "Associated symptoms",
                "min_met": 3,
                "criteria": [
                    {"key": "B1", "label": "Restlessness or feeling on edge"},
                    {"key": "B2", "label": "Easily fatigued"},
                    {"key": "B3", "label": "Difficulty concentrating"},
                    {"key": "B4", "label": "Irritability"},
                    {"key": "B5", "label": "Muscle tension"},
                    {"key": "B6", "label": "Sleep disturbance"},
                ],
            },
        ],
        "gates": [
            {"key": "duration", "label": "Present more days than not for at least six months"},
            {"key": "impairment", "label": "Causes meaningful distress or impairment"},
            {
                "key": "not_substance_medical",
                "label": "Not better accounted for by a substance or medical condition",
            },
            {
                "key": "not_better_explained",
                "label": "Not better explained by another mental disorder",
            },
        ],
        "icd10_options": [
            {"code": "F41.1", "label": "Generalized anxiety disorder"},
        ],
    },
}

# Bundled definitions, in display order.
BASELINE_DEFINITIONS: list[dict[str, Any]] = [_MDD, _GAD]
