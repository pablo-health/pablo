# Diagnostic-criteria assessment engine

Status: **Phase 1 (backend) implemented** · Audience: prescribing and
diagnosing clinicians (e.g. psychiatric NPs, physicians, licensed
diagnosticians)

## Problem

The `outcome_measures` store (PHQ-9, GAD-7, …) records *continuous symptom
scores* trended over time. It does not capture the other half of clinical
documentation: a **structured diagnostic determination** — "the patient meets
criteria for Major Depressive Disorder, F32.x." For a prescriber that
determination is the medical-necessity / prescription-justification record that
belongs in the chart and on the claim. It is categorical, point-in-time, and
rule-based (count of criteria met within groups, plus duration / impairment /
exclusion gates), not a number on a severity band — so it gets its own shape.

| | outcome measure | diagnostic assessment |
|---|---|---|
| Input | per-item ordinal responses (0–3) | per-criterion booleans + gate attestations |
| Scoring | sum → severity band | count-per-group ≥ threshold, all gates true |
| Output | total + severity | boolean determination + ICD-10-CM code + label |
| Read pattern | trended (sparkline) | recorded; current diagnosis list |

## Content posture (load-bearing)

Diagnostic *criteria wording* in the major manuals is restricted: DSM-5 text is
APA-copyrighted, and ICD-11 is licensed CC BY-ND (no derivatives — so even
paraphrasing it is disallowed). Therefore the bundled criterion labels are
**independently authored from the underlying clinical facts** (symptom
concepts, thresholds, durations — which are not copyrightable), not copied or
paraphrased from any manual. They are intended for **clinical review per
deployment** before clinical use.

**ICD-10-CM codes and descriptions are public domain** (US gov, NCHS/CMS) and
used directly. The determination's billing code is **clinician-confirmed** from
the definition's options — the engine never algorithmically maps one
classification onto another (which would be a crosswalk with its own
licensing). DSM-5 and ICD-11 may be consulted as references for accuracy; their
text is not shipped.

## Architecture

Definitions are **data**; a single metadata-driven evaluator is the only logic
in code. Three tables across two schemas:

```
PLATFORM (shared) schema — global reference data, one copy
  icd10_codes            code · description · billable · category
  diagnostic_definitions id · code · version · display_name
                         · evaluator_type · params(jsonb) · suggested_icd10 · active
                         UNIQUE(code, version)

PER-TENANT (practice_{id}) schema — patient data
  diagnostic_assessments id · patient_id · session_id · appointment_id
                         · instrument · definition_version
                         · criterion_responses(jsonb) · gate_responses(jsonb)
                         · meets_criteria · determined_icd10 · diagnosis_label
                         · criterion_citations(jsonb) · source · confirmed_at
                         · assessed_at · created_by/created_at/updated_at/deleted_at
```

Platform tables are created via `PlatformBase.metadata.create_all` and seeded
idempotently from `app.diagnostics.baseline` (env.py bootstrap). The per-tenant
table ships via an Alembic migration and is auto-covered by
`enable_rls_on_schema`'s `patient_id` policy arm (`has_patient_access`), same as
`notes` / `outcome_measures` — no hand-written policy.

### Single evaluator, metadata-driven

```python
# definition metadata selects the strategy; params hold the rule structure
evaluator_type: "criteria"      # closed vocabulary, implemented in code
params: { criterion_groups, gates, icd10_options }

def evaluate(definition, criterion_responses, gate_responses) -> DiagnosticOutcome:
    # dispatch on evaluator_type → one of a small fixed set of strategies
```

One entry point dispatches on `evaluator_type` to a *closed* set of strategy
functions. The diagnostic engine implements `"criteria"` (every group reaches
its `min_met`, satisfies any cardinal requirement, and every gate is true).
`"sum_scale"` / `"risk_stratifier"` are siblings for the outcome-measure and
C-SSRS families. New rule *shapes* extend this vocabulary in reviewed code —
deliberately **not** a stored expression language. Adding a disorder is a new
`diagnostic_definitions` row, not new code.

### Why snapshot the determination

Each assessment stores `definition_version` + the computed `meets_criteria`,
`determined_icd10`, and `diagnosis_label`. A diagnosis is a clinical/billing
record: it must reflect what was decided *then*, even after the definition is
later edited. The chart lists current determinations without re-evaluating.

## Scope

The **engine + a baseline of common diagnoses** (MDD, GAD) ships as usable,
configurable-per-deployment infrastructure: definitions are data, so a
deployment may add, version, or override them. `criterion_citations` +
`confirmed_at` ship now but are unused — reserved for future
provenance-tracked capture (recording which source supports each criterion,
and a clinician confirmation step) so that capability needs no migration.

## Phasing

1. **Backend** — tables, migration, seed, single evaluator (`criteria`),
   schemas, service, repository, API. *(done)*
2. **Frontend** — criterion form + live determination + Diagnoses chart surface.
3. **More definitions** — additional disorders as registry data, no schema
   change.
