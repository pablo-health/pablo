# Note-type derive eval

A clinician can hand Pablo one to three notes they have already written, or
describe their notes in a sentence or two, and
`app.services.note_type_derive_service` proposes a note type from them. It
returns the proposal unsaved, along with two pieces of evidence: which
passages of each sample found no field (coverage), and which parts of the
proposal were changed because they repeated a sample (the copied-text
guard).

The unit tests (`backend/tests/test_note_type_derive.py`) prove the
machinery with a scripted model. This eval asks whether it works with the
real one: does the model mirror the samples' structure, does the guard
leave no sample text behind, and does the coverage check notice text that
does not belong?

## What it grades

Hard failures, any one of which fails the run:

- **Structure.** The proposal must have each of the case's expected parts,
  in order (`sections` in `cases.py`; a part matches on any of its words in
  a section's label or its fields' labels).
- **No sample text.** No label, hint, description or prompt may repeat a
  sample: the same deterministic check the service runs (shared word runs,
  and a sample's names copied alone) must find nothing, and none of the
  case's `sentinels` (words that occur only in its samples) may appear
  anywhere in the proposal.
- **Coverage notices a stray.** A held-out note in the same format, never
  shown to the model, carries one passage no field of such a note is meant
  for. Checked against the proposal, that passage must come back unplaced.
- **A seeded proposal is caught.** `guard-seeded` answers the proposal call
  with a proposal that copies its sample (whole sentences in hints, a name
  in the description, a quote in the prompt); the rewrite and extraction
  calls are still the model's. The guard must find the copies and the final
  proposal must be clean. This grades the guard and the live rewrite whether
  or not the model would ever copy on its own.

Reported, never gated: passages of the samples themselves left unplaced
(each is a field the proposal lacks), other held-out passages left
unplaced, and how many parts the guard changed.

## Cases

All synthetic, written for this file about no one (see the no-real-PHI rule
in `backend/evals/README.md`).

| Case | Input | Checks |
|---|---|---|
| `psych-follow-up-sample` | a psychiatric follow-up with five headed parts | structure, guard, held-out stray |
| `dap-sample` | a DAP note in capitals | structure, guard, held-out stray |
| `guard-tempted` | the psychiatric sample plus a description asking for quoted examples in every hint | structure, guard; notes when the model declined to copy |
| `guard-seeded` | the psychiatric sample, proposal seeded with its text | the guard must catch and clear it |
| `description-only` | a plain-English description, no sample | structure |

**Captured sample: not yet.** The acceptance case for this feature is a
clinician's real note, scrubbed, proving the proposal has that note's
sections in order. No such note has been supplied yet. When one is, it joins
`ALL_CASES` as its own case (scrubbed of identifiers, structure kept intact)
with its headings as `sections`. Until then the psychiatric follow-up case
stands in for it.

## Recorded runs — 2026-10-05

Model as configured (`ai_model`, `gemini-3.1-pro-preview`, for the proposal;
`ai_model_flash` for extraction), against `pablohealth-dev`.

- Every case passed structure and the guard in every run (three runs of
  `guard-seeded`, which changed 5 parts each time and left none copied).
- `guard-tempted`: the model declined to copy every time, despite being
  asked; the guard had nothing to do. That is why `guard-seeded` exists.
- `dap-sample`'s held-out stray ("The front desk validated parking for the
  visit.") was placed in the Data field in 2 of 6 runs (and left out in
  3 further extractions run on their own). A DAP note's Data
  part is broad, so a stray aside can read as session data; the
  psychiatric case's stray was left out every time. The check stays hard
  so a change that makes this worse shows up.

An earlier version of the coverage extraction reused the import prompt's
rule to put a detail with no matching heading "under the field whose
meaning fits best". That rule is right for import (keep every word) and
wrong here (it hides every gap); the first DAP run caught it, and the
coverage extraction now leaves such text out.

## Running it

```bash
export GOOGLE_CLOUD_PROJECT=<a project with Vertex access>
gcloud auth application-default login       # once

scripts/run-note-type-derive-eval.sh              # every case
scripts/run-note-type-derive-eval.sh --list       # the cases, no model calls
scripts/run-note-type-derive-eval.sh --case guard
scripts/run-note-type-derive-eval.sh --json
```

A case makes three to four model calls; a full run takes about two minutes.
