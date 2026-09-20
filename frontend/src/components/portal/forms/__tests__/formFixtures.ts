// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The two responses the forms module reads, copied from what the routes
 * actually assemble.
 *
 * Copied rather than invented. The module's whole design is that the server
 * owns the wording and the shape, so a fixture its author wrote from memory
 * would only confirm the author's idea of both. The assignment here is the
 * form every practice is seeded with — demographics, reason, PHQ-9, GAD-7,
 * in that order — as `backend/app/db/intake_seed.py` lays it down and
 * `IntakeAssignmentDetailResponse` serialises it. The intake form response
 * is `backend/app/routes/patient_intake.py` plus the item text the registry
 * publishes.
 */

import type {
  IntakeAssignment,
  IntakeAssignmentDetail,
  IntakeAssignmentItem,
  IntakeForm,
  IntakeReceipt,
} from "@/lib/api/patientIntake"

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

/** The route keys a measure's items by their 1-based position. */
function keyed(items: string[]): Record<string, string> {
  return Object.fromEntries(items.map((text, index) => [String(index + 1), text]))
}

export const PHQ9_ITEM_COUNT = PHQ9_ITEMS.length
export const GAD7_ITEM_COUNT = GAD7_ITEMS.length

export const INTAKE_FORM: IntakeForm = {
  identity: { first_name: "Dana", last_name: "Okonkwo", date_of_birth: "1988-04-02" },
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

export const ASSIGNMENT_ID = "a4f1c3d2-0b56-4e78-9a1c-2d3e4f5a6b70"

export const ITEM_IDS = {
  demographics: "11111111-1111-4111-8111-111111111111",
  reason: "22222222-2222-4222-8222-222222222222",
  phq9: "33333333-3333-4333-8333-333333333333",
  gad7: "44444444-4444-4444-8444-444444444444",
}

function item(
  id: string,
  key: string,
  position: number,
  itemType: string,
  config: Record<string, unknown> = {},
  label: string | null = null,
  helpText: string | null = null,
): IntakeAssignmentItem {
  return {
    id,
    key,
    position,
    item_type: itemType,
    required: true,
    label,
    help_text: helpText,
    config,
    value: null,
  }
}

/**
 * One question the practice wrote itself, in the shape the route serves it.
 *
 * Every seeded item is one the engine words, so the fixture above cannot
 * stand in for an authored one: the whole difference is that the wording
 * rides on the row rather than arriving in the form response.
 */
export function authoredItem(
  itemType: string,
  {
    id = "77777777-7777-4777-8777-777777777777",
    key = "authored",
    label = "How have you been sleeping?",
    helpText = null,
    config = {},
  }: {
    id?: string
    key?: string
    label?: string | null
    helpText?: string | null
    config?: Record<string, unknown>
  } = {},
): IntakeAssignmentItem {
  return item(id, key, 0, itemType, config, label, helpText)
}

export const SEEDED_ITEMS: IntakeAssignmentItem[] = [
  item(ITEM_IDS.demographics, "demographics", 0, "demographics"),
  item(ITEM_IDS.reason, "reason", 1, "reason"),
  item(ITEM_IDS.phq9, "phq9", 2, "instrument", { code: "phq9" }),
  item(ITEM_IDS.gad7, "gad7", 3, "instrument", { code: "gad7" }),
]

export const ASSIGNMENT: IntakeAssignment = {
  id: ASSIGNMENT_ID,
  version_id: "9c8b7a65-4321-4098-8765-4321fedcba09",
  packet_name: "Intake",
  version: 1,
  status: "assigned",
  assigned_at: "2026-09-19T14:00:00Z",
  submitted_at: null,
  receipt_code: null,
  progress: { complete: false, missing: SEEDED_ITEMS.map((row) => row.id) },
}

/** One assignment detail, with whatever has been saved and is outstanding. */
export function assignmentDetail(
  overrides: Partial<IntakeAssignmentDetail> = {},
): IntakeAssignmentDetail {
  return { ...ASSIGNMENT, items: SEEDED_ITEMS, correction: null, ...overrides }
}

export const RECEIPT: IntakeReceipt = {
  assignment_id: ASSIGNMENT_ID,
  version_id: ASSIGNMENT.version_id,
  submitted_at: "2026-09-20T09:30:00Z",
  receipt_code: "K3MTQ7BX",
  measures: [
    { id: "om-phq9", instrument: "phq9", total_score: 12, severity: "moderate" },
    { id: "om-gad7", instrument: "gad7", total_score: 7, severity: "mild" },
  ],
}
