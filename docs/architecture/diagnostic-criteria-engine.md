# Diagnostic-criteria assessment engine

Status: **proposed** · Audience: prescribing and diagnosing clinicians
(e.g. psychiatric NPs, physicians, licensed diagnosticians)

## Problem

The `outcome_measures` store (PHQ-9, GAD-7, …) records *continuous symptom
scores* trended over time. It does not capture the other half of clinical
documentation: a **structured diagnostic determination** — "the patient meets
criteria for Major Depressive Disorder, F32.x." For a prescriber, that
determination is the medical-necessity and prescription-justification record
that belongs in the chart and on the claim. It is categorical, point-in-time,
and rule-based (count of criteria met within groups, plus duration / impairment
/ exclusion gates), not a number on a severity band.

This is a different *kind* of instrument and gets a different structure.

## Why not reuse `outcome_measures`

| | outcome measure | diagnostic assessment |
|---|---|---|
| Input | per-item ordinal responses (0–3) | per-criterion booleans (met / not met) + gates |
| Scoring | sum → severity band | count-per-group ≥ threshold, all gates true |
| Output | integer total + severity label | boolean determination + ICD-10 code + label |
| Read pattern | trended over time (sparkline) | recorded / superseded; current diagnosis list |

Forcing booleans-plus-gates into `total_score + severity` would be lossy and
clinically misleading. Per the "different DB structures are fine" call, this
gets its own table.

## Copyright constraint (load-bearing)

The DSM-5 **criterion wording** is copyrighted by the American Psychiatric
Association. This repository is AGPL and world-readable, so it **must not ship
verbatim DSM-5 criterion text**. What it *can* ship:

- the **engine** (criterion groups, count thresholds, gates, evaluation),
- **ICD-10 codes** (a public code set),
- **neutral, self-authored criterion labels** or placeholders.

The actual criterion text is supplied / confirmed per deployment by the
clinician or practice. "DSM-5 support" here means the diagnostic *structure*
and code mapping, never a bundled copyrighted content pack.

## Model

Mirrors the registry-vs-row split that `outcome_measures` already uses:
definitions live in code, recorded assessments live in a per-tenant table.

### Definition layer (code, like `instruments.py`)

```
DiagnosticCriteriaDefinition
  code: str                      # "dsm5_mdd"
  display_name: str              # "Major Depressive Disorder"
  criterion_groups: [CriterionGroup]
  gates: [Gate]                  # duration / impairment / exclusion attestations
  icd10: ICD10Mapping            # base code, or severity/specifier-conditioned

CriterionGroup
  key: str                       # "A"
  criteria: [Criterion]          # each: key, label (neutral/placeholder), optional cardinal flag
  min_met: int                   # e.g. 5
  require_cardinal: bool         # e.g. ≥1 of the cardinal criteria must be met

Gate
  key: str                       # "duration" | "impairment" | "exclusion_substance" | …
  label: str
  must_be: bool                  # attestation required for the diagnosis to hold
```

Evaluation:

```
evaluate(defn, criterion_responses, gate_responses) -> DiagnosticOutcome
  meets_criteria: bool           # all groups satisfy min_met (+ cardinal) AND all gates true
  icd10_code: str | None
  diagnosis_label: str | None
```

### Record layer (new per-tenant table `diagnostic_assessments`)

Same conventions as `outcome_measures` (lives in `practice_{id}` schema,
app-layer `has_patient_access`, soft-delete):

```
id                uuid pk
patient_id        uuid  (indexed; access via has_patient_access)
instrument        str   ("dsm5_mdd")
criterion_responses  jsonb   {criterion_key: bool}
gate_responses       jsonb   {gate_key: bool}
meets_criteria    bool        (computed, stored)
determined_icd10  str | null  (computed, stored)
diagnosis_label   str | null
source            str         (manual | …)  -- reuse OutcomeMeasureSource? or own enum
assessed_at       timestamptz
created_by        str
created_at / updated_at / deleted_at
```

`meets_criteria` / `determined_icd10` are computed at write time and stored so
the chart can list current diagnoses without re-evaluating; they are also
re-derivable from the definition + responses.

> RLS note: this is a new per-tenant table carrying `patient_id`, so it falls
> under the L4 RLS-coverage guard. Follow whatever `outcome_measures` / `notes`
> settled on (app-layer `has_patient_access`, no separate policy) and confirm
> the coverage check passes before landing.

## API (mirrors outcome_measures)

```
POST   /api/patients/{patient_id}/diagnostic-assessments   -> 201
GET    /api/patients/{patient_id}/diagnostic-assessments   -> list
GET    /api/diagnostic-assessments/{id}
DELETE /api/diagnostic-assessments/{id}                    -> 204 (soft-delete)
```

Request carries `instrument`, `criterion_responses`, `gate_responses`,
`assessed_at`, `source`. Response adds the computed `meets_criteria`,
`determined_icd10`, `diagnosis_label`.

## Frontend

A criterion checklist form: group headings, met/not-met per criterion, the gate
attestations, and a live determination panel ("Criteria met — F32.9" or
"Criteria not met: group A needs ≥5, 3 selected"). A "Diagnoses" surface on the
chart lists current determinations with their codes. The clinician can edit
criterion labels their deployment supplies.

## AI-assisted criterion drafting (human-in-the-loop)

The product already generates SOAP notes from session transcripts with an LLM.
The same capability can **draft** a diagnostic assessment: given a transcript,
SOAP note, or other note, the model proposes which criteria appear supported
and attaches a **citation** to the source span for each — so the clinician
verifies rather than trusts. The clinician then confirms, edits, or rejects.

The data model already anticipates this:

- **`source = "inferred"`** is an existing capture-source value — i.e. an
  AI-derived assessment a clinician has not yet confirmed.
- **`item_citations` / criterion citations** carry per-criterion provenance
  (which source span supports each proposed "met").

Non-negotiable guardrails:

- **The AI drafts; the clinician determines.** A diagnosis is a licensed act.
  An `inferred` assessment is a *draft* and never the final record on its own;
  it becomes authoritative only when a clinician confirms it (source flips to
  `manual`/clinician-confirmed, `created_by` is the clinician).
- **Every proposed criterion carries a citation** back to the source text.
  Uncited "met" suggestions are not shown as met. This is the defense against a
  hallucinated criterion silently entering a diagnosis.
- **PHI handling unchanged.** The draft call processes PHI (transcript / note)
  and must go through the existing LLM-gateway + audit/redaction seams; no PHI
  in logs (OSS guardrail). Reuse the SOAP-generation path's posture.

Drafting is an *engine* capability (OSS already calls the LLM gateway for SOAP),
exposed as a separate endpoint that returns a non-persisted draft the form
pre-fills; nothing is stored until the clinician saves.

## Phasing

1. **Backend engine + model + migration + API** with one worked definition
   (MDD), neutral criterion labels, ICD-10 mapping. Tests for the evaluator
   (threshold, cardinal requirement, gates) and the round-trip.
2. **Frontend** criterion form + determination display + chart surface.
3. **More definitions** (GAD, etc.) as data — no schema change.
4. **AI-assisted drafting** — endpoint that takes a note/transcript ref and
   returns proposed criterion responses + citations (`source=inferred`); the
   form pre-fills them for clinician confirmation. Depends on phases 1–2.

## Explicitly out of scope (separate work)

- C-SSRS risk stratifier + generic/custom instrument — the original
  instrument-registry track; sequenced after this.
- DSM-5 Level 1/2 Cross-Cutting *screeners* (distinct from diagnostic
  criteria) — backlog; lower value for practicing clinicians.
- Severity specifiers / full ICD-10 sub-coding beyond the base code — later.
