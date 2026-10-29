// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The medical decision making review's inputs. Mirrors
 * `REVIEW_INPUTS` in backend/app/notes/mdm_review.py.
 *
 * A note type that declares them is reviewed in the MDM panel, which sets
 * them; the model never reads them, so changing one drafts nothing again.
 * They are not asked for when booking or under the note's details.
 */
export const MDM_CHOICE_INPUTS = {
  problems: "mdm_problems",
  data: "mdm_data",
  risk: "mdm_risk",
} as const

export const NEW_PATIENT_INPUT = "new_patient"

const REVIEW_INPUT_KEYS: readonly string[] = [...Object.values(MDM_CHOICE_INPUTS), NEW_PATIENT_INPUT]

/** Whether an input belongs to the MDM review rather than to the draft. */
export function isReviewInput(key: string): boolean {
  return REVIEW_INPUT_KEYS.includes(key)
}

/** Whether a note type is reviewed in the MDM panel: it declares all three choices. */
export function offersMdmReview(inputs: Array<{ key: string }>): boolean {
  const declared = new Set(inputs.map((i) => i.key))
  return Object.values(MDM_CHOICE_INPUTS).every((key) => declared.has(key))
}

/** "moderate" -> "Moderate": a level as the panel shows it. */
export function levelLabel(level: string): string {
  return level.charAt(0).toUpperCase() + level.slice(1)
}
