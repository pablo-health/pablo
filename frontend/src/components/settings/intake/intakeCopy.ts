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

/**
 * Licensed instruments — the measures a practice needs permission to use.
 *
 * The section says what the restriction is and stops. It does not argue the
 * copyright position or tell a practice what its licence covers: the
 * publisher's own line is beside each measure, with a link where there is a
 * page to read, and the practice is the one who knows what it holds.
 */
export const LICENSED_TITLE = "Licensed instruments"
export const LICENSED_DESCRIPTION = "Measures whose publisher restricts how they may be used."
export const LICENSED_EMPTY = "Nothing here needs your permission."

/** The whole claim a practice makes. No more than this. */
export const LICENSED_CHECKBOX = "Our practice holds the permission required to use this instrument"

export const LICENSE_REFERENCE_LABEL = "License reference (optional)"
export const LICENSE_REFERENCE_PLACEHOLDER = "Anything that helps you find it again"
export const LICENSED_SAVE = "Save"
export const LICENSED_WITHDRAW = "Withdraw"
export const LICENSED_ON_FILE = "On file"

/**
 * Shown where a measure is sold rather than restricted.
 *
 * It says what to do instead. A practice that holds one of these licences is
 * not blocked from using it — the form goes on as an upload, and the score is
 * theirs to record.
 */
export const SOLD_TITLE = "Measures you buy from their publisher"
export const SOLD_GUIDANCE =
  "Ask for your own copy as a document upload, and record the score yourself."

/** Shown under the measure picker beside one the practice has not licensed. */
export const MEASURE_NEEDS_PERMISSION =
  "Record your permission under Licensed instruments to add this one."

/**
 * The question itself, and the line under it.
 *
 * Two fields on every question a practice writes. The placeholder says what
 * goes in the box rather than that the box is required — the server refuses
 * the publish and names the question, which is the moment it matters.
 */
export const LABEL_FIELD = "Question"
export const LABEL_PLACEHOLDER = "Question the patient will see"
export const HELP_TEXT_FIELD = "Help text (optional)"
export const HELP_TEXT_PLACEHOLDER = "Anything that helps them answer it"

/** The label field on the questions the engine already words. */
export const LABEL_FIELD_OVERRIDE = "Heading (optional)"
export const LABEL_OVERRIDE_PLACEHOLDER = "Leave empty to ask it as Pablo words it"

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

export const DOCUMENTS_TITLE = "Documents"
export const DOCUMENTS_DESCRIPTION = "What people read and sign before you see them."
export const DOCUMENTS_EMPTY = "No documents yet."
export const NEW_DOCUMENT_NAME = "New document"
export const ADD_DOCUMENT = "Add a document"
export const DOCUMENT_NAME_LABEL = "Name"
export const DOCUMENT_BODY_LABEL = "What they read"
export const DOCUMENT_PREVIEW_LABEL = "Preview"
export const NEW_DOCUMENT_VERSION_BUTTON = "Start a new version"

/**
 * Shown on a published version instead of the editor.
 *
 * Same shape as the form notice: what to do, not what is forbidden. A
 * published document is what somebody's signature was a signature to, so a
 * change goes on a new version.
 */
export const DOCUMENT_PUBLISHED_NOTICE =
  "This version has been published. Start a new version to change the wording."

/** Shown under the picker when the practice has nothing published to pick. */
export const NO_PUBLISHED_DOCUMENTS = "Publish a document first, then you can ask for it here."

export const DOCUMENT_PICKER_LABEL = "Which document"

/**
 * The settings on a question that asks for a photo of an insurance card.
 *
 * "Both sides" is the default because a plan's details are printed across
 * the two, and a practice that only needs the front will say so.
 */
export const CARD_SIDES_LABEL = "How many photos"
export const CARD_SIDES_BOTH = "Front and back"
export const CARD_SIDES_FRONT = "Front only"
export const CARD_COLLECT_FIELDS_LABEL = "Also ask them to type the plan details"
export const CARD_COLLECT_FIELDS_HELP =
  "Payer, member ID and group number. These go on the client's coverage record."

/**
 * The paper fallback on a question that asks for a document back.
 *
 * Optional, and described as what it is: the question works without one,
 * and most of what it asks for is something the client already has.
 */
export const BLANK_FORM_PICKER_LABEL = "Offer a form to download (optional)"
export const NO_BLANK_FORM_CHOICE = "Don't offer one"
export const NO_BLANK_FORMS = "Upload a blank form first, then you can offer it here."

/**
 * Asking a question only of the people it applies to.
 *
 * The picker says what it does and stops. It does not explain that a rule
 * can only look backwards, or what happens to an answer somebody gives and
 * then makes irrelevant — the first is enforced by which questions the
 * picker offers, and the second is the patient's receipt to carry, not a
 * paragraph for a therapist building a form.
 */
export const VISIBILITY_TITLE = "Show only when"
export const VISIBILITY_ALWAYS = "Always ask this"
export const VISIBILITY_QUESTION_LABEL = "Based on"
export const VISIBILITY_CONDITION_LABEL = "When they"
export const VISIBILITY_VALUE_LABEL = "Value"
export const VISIBILITY_ITEM_NUMBER_LABEL = "Question number"

/** Shown on the first question on a form, which has nothing to depend on. */
export const VISIBILITY_NOTHING_EARLIER =
  "This is the first question that can be answered, so there is nothing to base it on yet."
