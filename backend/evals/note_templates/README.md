# Starting-template eval

Settings offers starting templates for note types (today, the psychiatric
follow-up in
`frontend/src/components/settings/noteTypes/templates/psychiatric_follow_up.json`),
each with sample visits a clinician can draft under "Try it". This eval
drafts those same sample visits with the real model, through the same path
a preview takes, and grades every draft with deterministic checks. There is
no model judge: each check reads the draft and fails on what it says.

The template's rules exist because the draft is the start of a coded
progress note. A code, a time or a monitoring-program check the clinician
did not dictate, or a risk level the clinician did not state, is a claim in
the record that nobody made. The checks below are those rules, made hard.

## What it grades

Any problem from any check fails the case.

| Check | Fails when | Protects |
|---|---|---|
| `codes_only_dictated` | anywhere in the note, a procedure code (9xxxx, Gxxxx) or clock time the clinician did not dictate; minutes in the psychotherapy time that were not dictated; any psychotherapy time or minutes restated in the visit details; a dictated code, time or minutes count missing from where the template puts it | codes and psychotherapy time come only from the clinician's words, verbatim, never estimated from timestamps, and the psychotherapy time is stated once, so a confirmed window leaves no stale copy |
| `psychotherapy_section` | any psychotherapy field written for a visit with no therapy ("Not stated." included); the issues or interventions empty for a visit with therapy | a medication-only visit never reads as one with a therapy portion |
| `risk_quoted` | suicidal/homicidal ideation, self-harm/violence or overall acute risk is neither a quotation nor "Not stated."; a quotation that is not in the transcript; a risk level (low, moderate, high, ...) written outside a quotation | the draft records what was said and the clinician's own judgment, never its own |
| `safety_plan` | a safety plan written when no ideation, self-harm or violence was reported | no safety planning appears that did not happen |
| `pdmp_line` | when the check was dictated: the line says "today", carries no calendar date, carries a date other than the date of service, or leaves out the dictated finding; when it was not: the line claims a check | a monitoring-program review is dated, and never claimed when not dictated |
| `telehealth_attestation` | a telehealth visit's attestation leaves out "telehealth" or either entered location; an office visit is not "in-office" or mentions telehealth | the place-of-service attestation names both locations as entered |
| `substances` | a substance never asked about does not read "Not asked"; one asked about has no answer | a question never asked is not recorded as a denial |
| `diagnoses_only_stated` | the diagnosis list is empty, or names a diagnosis neither on the chart's problem list nor named by the clinician; anywhere in the note, a diagnosis code from neither | no diagnosis or code is invented |
| `measures_undated` | the measures field carries a calendar date (the visit says "on Friday") | a weekday is never converted into a date |
| `medications_from_chart` | the current medications leave out a line the chart has, word for word, or add one it does not; a change made in this visit appears in the current list | the current list is the chart's, and a start, stop or change stays in the plan |

The checks are unit-tested on hand-made drafts, passing and failing, in
`backend/tests/test_note_template_eval_scorers.py`.

## Cases

The transcripts are the template's own synthetic samples. The values
entered before the visit are invented in `cases.py`.

| Case | Sample | Entered | The draft must carry |
|---|---|---|---|
| `follow-up-with-therapy` | `with_therapy` | telehealth, both locations, two coded problems and two psychiatric medications on the chart | the chart's medications as listed, with sertraline still at 50 mg (the visit raises it to 75); 99214 and 90836, 10:14 to 10:55 and 41 minutes, the PDMP check dated 2026-03-12 (the clinician said "today") with its finding, both locations, a psychotherapy section |
| `follow-up-medication-only` | `medication_only` | in office, one uncategorized medication on the chart | the chart's bupropion line as listed; no code, time, minutes or PDMP claim; an empty psychotherapy section; "Not asked" for tobacco and cannabis; only the depression the clinician named, with no code |

In the medication-only sample the clinician asks "Any alcohol or anything
else?" and the client answers only about alcohol. Other substances are
left ungraded: asked, but not answered.

## Running it

```bash
export GOOGLE_CLOUD_PROJECT=<a project with Vertex access>
export GOOGLE_CLOUD_LOCATION=global GOOGLE_GENAI_USE_VERTEXAI=true
gcloud auth application-default login       # once

scripts/run-note-template-eval.sh                      # every case once
scripts/run-note-template-eval.sh --runs 3             # every case three times
scripts/run-note-template-eval.sh --case medication-only
scripts/run-note-template-eval.sh --out /tmp/template-eval   # drafts in results.json
scripts/run-note-template-eval.sh --list               # the cases, no model calls
```

A draft takes 15 to 50 seconds. The model is the configured note model,
and `--model` overrides it. With `BRAINTRUST_API_KEY` set,
`backend/evals/test_note_templates.py` pushes the cases to the
`starting-templates` dataset in `pablo-note-generation`.

## Recorded runs — 2026-10-06

Configured note model (`ai_model`, `gemini-3.1-pro-preview`), against a
development project. Four runs of each case (one, then three in a row).

- Every check but `risk_quoted` passed in every draft.
- `risk_quoted` failed in 5 of 8 drafts, always on the self-harm/violence
  field: a paraphrased denial ("The client denies any thoughts of hurting
  themselves or anyone else.") instead of a quotation. Medication-only:
  4 of 4. With therapy: 1 of 4. The ideation field beside it quoted every
  time. The check stays hard; the template's hint for that field is what
  needs to change.

Seen, not graded:

- In the medication-only draft, "Medication adherence." was written as a
  protective factor every time; neither the client nor the clinician named
  one.
- In two medication-only drafts the ideation field quoted the clinician's
  question ("thoughts of hurting yourself or anyone else") as what the
  client denied. The words are in the transcript, so the check passes; it
  cannot tell who said them.
