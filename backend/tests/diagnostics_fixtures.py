# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Synthetic diagnostic definitions for the engine tests.

Deliberately non-clinical, made-up content (placeholder labels and codes) — the
tests exercise the metadata-driven evaluator (count thresholds, the cardinal
rule, gate attestation, code validation) without depending on any real
diagnostic criteria. Definitions are data; these fixtures stand in for the rows
a deployment would supply.
"""

from __future__ import annotations

from typing import Any

# A two-group definition: Group A needs 2 of 4 with a required cardinal among
# A1/A2; Group B needs 1 of 2; two gates; two offered codes.
SYNTHETIC_DEFINITION: dict[str, Any] = {
    "code": "synthetic",
    "version": 1,
    "display_name": "Synthetic Screen",
    "evaluator_type": "criteria",
    "suggested_icd10": "T00.1",
    "params": {
        "criterion_groups": [
            {
                "key": "A",
                "label": "Group A",
                "min_met": 2,
                "require_cardinal": True,
                "criteria": [
                    {"key": "A1", "label": "Alpha", "cardinal": True},
                    {"key": "A2", "label": "Bravo", "cardinal": True},
                    {"key": "A3", "label": "Charlie"},
                    {"key": "A4", "label": "Delta"},
                ],
            },
            {
                "key": "B",
                "label": "Group B",
                "min_met": 1,
                "require_cardinal": False,
                "criteria": [
                    {"key": "B1", "label": "Echo"},
                    {"key": "B2", "label": "Foxtrot"},
                ],
            },
        ],
        "gates": [
            {"key": "g1", "label": "Gate one"},
            {"key": "g2", "label": "Gate two"},
        ],
        "icd10_options": [
            {"code": "T00.1", "label": "Synthetic code one"},
            {"code": "T00.2", "label": "Synthetic code two"},
        ],
    },
}

# A second, simpler definition so list/instrument-filter tests have two codes.
SYNTHETIC_DEFINITION_2: dict[str, Any] = {
    "code": "synthetic2",
    "version": 1,
    "display_name": "Synthetic Screen Two",
    "evaluator_type": "criteria",
    "suggested_icd10": "T01.1",
    "params": {
        "criterion_groups": [
            {
                "key": "A",
                "label": "Group A",
                "min_met": 1,
                "require_cardinal": False,
                "criteria": [
                    {"key": "A1", "label": "Uno"},
                    {"key": "A2", "label": "Dos"},
                ],
            }
        ],
        "gates": [{"key": "g1", "label": "Gate"}],
        "icd10_options": [{"code": "T01.1", "label": "Synthetic two code"}],
    },
}

# A checklist-strategy definition (same shape as SYNTHETIC_DEFINITION) — the
# engine records responses and offers a code, but renders no pass/fail verdict.
SYNTHETIC_CHECKLIST: dict[str, Any] = {
    "code": "synthetic_checklist",
    "version": 1,
    "display_name": "Synthetic Checklist",
    "evaluator_type": "checklist",
    "suggested_icd10": "T00.1",
    "params": SYNTHETIC_DEFINITION["params"],
}

# A checklist definition that also carries the optional prescribing-support
# data (differentials / safeguards / medication rationale). Synthetic,
# non-clinical placeholder content — stands in for the reference data a
# deployment would supply on a definition.
SYNTHETIC_RX: dict[str, Any] = {
    "code": "synthetic_rx",
    "version": 1,
    "display_name": "Synthetic Rx Screen",
    "evaluator_type": "checklist",
    "suggested_icd10": "T00.1",
    "params": {
        **SYNTHETIC_DEFINITION["params"],
        "differentials": [
            {
                "icd_code": "T99.0",
                "mimics_how": "Looks alike because reasons.",
                "distinguish_how": "Tell apart via the other thing.",
                "transcript_cues": [
                    {"cue_text": "mentions the other thing", "citation": "Placeholder 2026"},
                    {"cue_text": "no citation cue"},
                ],
            }
        ],
        "prescribing_safeguards": [
            {
                "key": "registry_check",
                "label": "Registry check captured",
                "applies_when": "Synthetic scope",
                "citation": "Placeholder rule",
            }
        ],
        "medication_rationale": {
            "first_line": ["agent-a", "agent-b"],
            "alternatives": ["agent-c"],
            "stepped_care": "Start low, go slow.",
            "this_agent_now": "agent-a chosen for the placeholder reason.",
            "citations": ["Placeholder guideline 2026"],
        },
    },
}

SYNTHETIC_DEFINITIONS: list[dict[str, Any]] = [
    SYNTHETIC_DEFINITION,
    SYNTHETIC_DEFINITION_2,
    SYNTHETIC_CHECKLIST,
    SYNTHETIC_RX,
]

# Responses that satisfy SYNTHETIC_DEFINITION: Group A (A1 cardinal + A2),
# Group B (B1), and both gates.
SYNTHETIC_MET_CRITERIA: dict[str, bool] = {"A1": True, "A2": True, "B1": True}
SYNTHETIC_ALL_GATES: dict[str, bool] = {"g1": True, "g2": True}

# Responses that satisfy SYNTHETIC_DEFINITION_2.
SYNTHETIC2_MET_CRITERIA: dict[str, bool] = {"A1": True}
SYNTHETIC2_ALL_GATES: dict[str, bool] = {"g1": True}
