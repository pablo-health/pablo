// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Strings the form builder shows.
 *
 * Collected here so the tests can assert against the same constants the screen
 * renders, rather than against a copy of them that drifts.
 */

/** What each item type is called on the screen, and one line of what it does. */
export const ITEM_TYPE_LABELS: Record<string, string> = {
  section: "Section heading",
  instructions: "Instructions",
  demographics: "Name and date of birth",
  reason: "What brings you in",
  free_text: "Written answer",
  single_choice: "Pick one",
  multi_choice: "Pick any",
  yes_no: "Yes or no",
  scale: "Scale",
  number: "Number",
  date: "Date",
  instrument: "Measure",
  emergency_contact: "Emergency contact",
  guardian: "Parent or guardian",
  consent_document: "Consent to sign",
  insurance_card: "Insurance card",
  document_request: "Document upload",
}

export const ITEM_TYPE_HINTS: Record<string, string> = {
  section: "Groups the questions under it.",
  instructions: "Text to read. Nothing to answer.",
  demographics: "Asks them to confirm what the chart already says.",
  reason: "One written answer, in their own words.",
  free_text: "A box to write in.",
  single_choice: "One answer from a list you write.",
  multi_choice: "Any number of answers from a list you write.",
  yes_no: "Yes or no, with an optional box to say more.",
  scale: "A numbered scale with a word at each end.",
  number: "A number, with an optional range.",
  date: "A date.",
  instrument: "A scored measure, asked the way the measure asks it.",
  emergency_contact: "Who to call, and how.",
  guardian: "Who is answering on their behalf.",
  consent_document: "A document to read and sign.",
  insurance_card: "A photo of the card.",
  document_request: "Any other file you need.",
}

/** The measures a patient can complete themselves. Mirrors the server. */
export const SELF_REPORT_INSTRUMENTS: { code: string; label: string }[] = [
  { code: "phq9", label: "PHQ-9" },
  { code: "gad7", label: "GAD-7" },
]

export const FORMS_TITLE = "Forms"
export const FORMS_DESCRIPTION = "What your intake asks, and who it asks it of."
export const EMPTY_STATE = "No forms yet."
export const NEW_FORM_NAME = "New form"
export const DRAFT_BADGE = "Draft"
export const PUBLISHED_BADGE = "Published"
export const PUBLISH_BUTTON = "Publish"
export const ADD_QUESTION = "Add question"
export const NEW_VERSION_BUTTON = "Start a new version"
export const NO_QUESTIONS = "Nothing on this form yet."

/**
 * Shown on a published version instead of the editor.
 *
 * It says what to do rather than what is forbidden: a published form is what
 * someone's answers were answers to, so a change goes on a new version.
 */
export const PUBLISHED_NOTICE =
  "This version has been published. Start a new version to change the questions."
