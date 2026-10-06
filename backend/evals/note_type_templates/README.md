# Note-type template eval

Settings > Note types offers templates a practice can start from
(`frontend/src/components/settings/noteTypes/templates/`). Each ships with a
synthetic sample visit for "Try it". This eval drafts each case's sample with
the real model, through the same path a preview uses, and checks the draft
against what that visit actually contained.

`backend/tests/test_note_type_templates.py` proves a template is a definition
the server stores unchanged; `backend/tests/test_note_type_template_eval_grader.py`
proves the grading. This asks whether the template's prompts hold with the
model.

## What it grades

Hard failures, any one of which fails the run:

- **Covered fields are filled.** A field the visit or the clinician's
  dictation covered must not come back empty.
- **Gaps say so.** A field the visit did not cover must read "Not stated." or
  "Not asked." rather than something the model supplied.
- **Sections that must stay empty do.** The psychotherapy portion of a visit
  billed as a psychiatric diagnostic evaluation has no content.
- **Diagnoses as stated.** Every stated diagnosis is there with its code, no
  diagnosis carries a code the clinician never said, and a rule-out is marked
  as one.
- **Nothing invented.** No value holds a code or level the clinician never
  stated (an E/M level, a psychotherapy add-on, a substance use disorder code).

## Cases

| Case | Template | Sample |
|---|---|---|
| `psychiatric-evaluation-new-client` | `psychiatric_evaluation` | a new-client evaluation by video, with the clinician's dictated addendum; three diagnoses, one a rule-out; billed 90792 |

## Running it

Drafting calls Vertex. Use a project with Vertex access, never production:

```bash
export GOOGLE_CLOUD_PROJECT=<dev project> GOOGLE_CLOUD_LOCATION=global GOOGLE_GENAI_USE_VERTEXAI=true
scripts/run-note-type-template-eval.sh --out /tmp/template-drafts
```

`--out` keeps each draft as JSON for reading by eye.
