// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A `GET /api/patient/intake/form` response, copied from what the route
 * actually assembles (`backend/app/routes/patient_intake.py` +
 * `backend/app/outcome_measures/item_text.py`).
 *
 * Copied rather than invented: the form's whole design is that the server
 * owns the wording, so a fixture the form's author wrote would only confirm
 * the author's idea of the wording. Item keys are "1".."n" because the route
 * enumerates the registry's items, and the four anchors are the instruments'
 * published ones.
 */

import type { IntakeForm, IntakeSubmission } from "@/lib/api/patientIntake"

const FREQUENCY_PROMPT =
  "Over the last 2 weeks, how often have you been bothered by any of the following problems?"

const FREQUENCY_OPTIONS = [
  { value: 0, label: "Not at all" },
  { value: 1, label: "Several days" },
  { value: 2, label: "More than half the days" },
  { value: 3, label: "Nearly every day" },
]

const PHQ9_ITEMS = [
  "Little interest or pleasure in doing things",
  "Feeling down, depressed, or hopeless",
  "Trouble falling or staying asleep, or sleeping too much",
  "Feeling tired or having little energy",
  "Poor appetite or overeating",
  "Feeling bad about yourself — or that you are a failure or have let yourself or your family down",
  "Trouble concentrating on things, such as reading the newspaper or watching television",
  "Moving or speaking so slowly that other people could have noticed — or the opposite, being so fidgety or restless that you have been moving around a lot more than usual",
  "Thoughts that you would be better off dead, or of hurting yourself in some way",
]

const GAD7_ITEMS = [
  "Feeling nervous, anxious, or on edge",
  "Not being able to stop or control worrying",
  "Worrying too much about different things",
  "Trouble relaxing",
  "Being so restless that it is hard to sit still",
  "Becoming easily annoyed or irritable",
  "Feeling afraid, as if something awful might happen",
]

/** The route keys items by their 1-based position. */
function keyed(items: string[]): Record<string, string> {
  return Object.fromEntries(items.map((text, index) => [String(index + 1), text]))
}

export const PHQ9_ITEM_COUNT = PHQ9_ITEMS.length
export const GAD7_ITEM_COUNT = GAD7_ITEMS.length

export const INTAKE_FORM: IntakeForm = {
  identity: {
    first_name: "Dana",
    last_name: "Okonkwo",
    date_of_birth: "1988-04-02",
  },
  reason_prompt: "What brings you in?",
  instruments: [
    {
      code: "phq9",
      display_name: "PHQ-9",
      prompt: FREQUENCY_PROMPT,
      items: keyed(PHQ9_ITEMS),
      response_options: FREQUENCY_OPTIONS,
    },
    {
      code: "gad7",
      display_name: "GAD-7",
      prompt: FREQUENCY_PROMPT,
      items: keyed(GAD7_ITEMS),
      response_options: FREQUENCY_OPTIONS,
    },
  ],
}

/**
 * A 201 body, scores and all. They arrive; the done screen shows none of
 * them, which is what the test asserts against.
 */
export const INTAKE_SUBMISSION: IntakeSubmission = {
  id: "5f1c0f0e-9a1f-4b0b-8f2a-9c6d1f7d2e10",
  submitted_at: "2026-09-19T15:04:05Z",
  measures: [
    { id: "om-phq9", instrument: "phq9", total_score: 12, severity: "moderate" },
    { id: "om-gad7", instrument: "gad7", total_score: 7, severity: "mild" },
  ],
}
