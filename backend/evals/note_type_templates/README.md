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
- **The clinician's words, quoted.** A statement the case names (risk the
  clinician dictated after the client left, say) appears in its field inside
  quotation marks.
- **Nothing invented.** No value holds a code or level the clinician never
  stated (an E/M level, a psychotherapy add-on, a substance use disorder code).

## Cases

| Case | Template | Sample |
|---|---|---|
| `psychiatric-evaluation-new-client` | `psychiatric_evaluation` | a new-client evaluation by video, with the clinician's dictated addendum; three diagnoses, one a rule-out; billed 90792 |
| `psychiatric-follow-up-dictated-addendum` | `psychiatric_follow_up` | a short medication check by video, drafted as a two-channel call; risk and mental status are stated only in the addendum dictated after the client left, and must come back quoted; self-harm, orientation and cognition are covered nowhere and must read "Not stated." |
| `psychiatric-follow-up-therapy-start` | `psychiatric_follow_up` | a medication check, then a long therapy block the client asks for; every proposed therapy start falls after the medication check. The clinician dictates the minutes but no clock times; once the start is confirmed, the psychotherapy time field must read the confirmed window |
| `psychiatric-follow-up-redraft-keeps-facts` | `psychiatric_follow_up` | a medication check with a home blood pressure and a refill sent to the pharmacy, then therapy. Drafted, then drafted again with one more line dictated for the plan, as "Dictate more" does: the blood pressure, the supply and the pharmacy must survive, the dictated line must be added, and the redraft is graded like the first draft |

A case drafted as a recorded call splits its transcript where the client's
last line ends: what the clinician says after it reaches the model as a
separate addendum, exactly as a recorded session's does.

## Running it

Drafting calls Vertex. Use a project with Vertex access, never production:

```bash
export GOOGLE_CLOUD_PROJECT=<dev project> GOOGLE_CLOUD_LOCATION=global GOOGLE_GENAI_USE_VERTEXAI=true
scripts/run-note-type-template-eval.sh --out /tmp/template-drafts
```

`--out` keeps each draft as JSON for reading by eye.
