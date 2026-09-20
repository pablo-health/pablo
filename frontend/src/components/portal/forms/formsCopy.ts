// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Every sentence the portal's forms show a patient, in one file.
 *
 * Collected here because this is safety copy as much as it is UI copy, and
 * reviewing it should not mean reading a dozen components. The questions
 * themselves are NOT here — those come from the server, so the form and the
 * scorer cannot drift.
 */

/**
 * Shown on every measure, and again on the receipt.
 *
 * Always, never conditionally. Showing it only when someone answers a
 * particular item a particular way would imply that answers are being read
 * as they are typed, and they are not: the form is filled in, handed in, and
 * read by a clinician afterwards. A crisis line that appears in response to
 * an answer promises a monitor that does not exist.
 */
export const CRISIS_FOOTER =
  "If you're in crisis or thinking about harming yourself, call or text 988 (Suicide & Crisis Lifeline), or call 911 in an emergency."

/** The list of forms this patient has been asked for. */
export const LIST_HEADING = "Your forms"
export const LIST_EMPTY = "Nothing to fill in right now."
export const LIST_CONTINUE = "Continue"
export const LIST_START = "Start"
export const LIST_SENT = "Sent"
export const LIST_PROGRESS_DONE = "Ready to send"
export const LIST_WITHDRAWN = "No longer needed"

/** How much of a form is outstanding, from the count the server sent. */
export function questionsLeft(outstanding: number): string {
  return outstanding === 1 ? "1 question left" : `${outstanding} questions left`
}

/** Both dead ends a portal session can hand a form. */
export const EXPIRED_HEADING = "This link has expired"
export const EXPIRED_BODY = "Ask your clinician for a new invite link, then start again."

/** Everything else: worth another try. */
export const RETRY_HEADING = "Something went wrong"
export const RETRY_BODY = "We couldn't load your forms. Try again in a moment."
export const RETRY_ACTION = "Try again"

export const LOADING = "Loading your forms…"

/** Where the patient is in the questions that collect an answer. */
export function questionPosition(index: number, total: number): string {
  return `Question ${index} of ${total}`
}

/** Navigation through one form. */
export const BACK = "Back"
export const CONTINUE = "Continue"
export const EDIT = "Edit"
export const SAVING = "Saving…"

/** The review screen. */
export const REVIEW_HEADING = "Check your answers"
export const REVIEW_BODY = "Have a look before you send this to your clinician."
export const REVIEW_UNANSWERED = "Not answered yet"
export const SUBMIT = "Send to my clinician"
export const SUBMITTING = "Sending…"

/**
 * The receipt. No totals and no bands: a PHQ-9 total is a number with a
 * clinical meaning attached, and the person qualified to attach it is the
 * clinician reading the chart — not a page a patient meets alone, minutes
 * after answering nine questions about how bad the last fortnight has been.
 */
export const RECEIPT_HEADING = "All done"
export const RECEIPT_BODY = "Thanks. Your responses have been sent to your clinician."
export const RECEIPT_CODE_LABEL = "Your reference"
export const RECEIPT_CODE_NOTE =
  "Keep this if you'd like — you and your practice can use it to find this form."
export const RECEIPT_CLOSE = "Back to your forms"

/** Failures that happen while a form is open. */
export const SAVE_FAILED = "We couldn't save that. Try again in a moment."
export const SUBMIT_FAILED = "We couldn't send your answers. Try again in a moment."
export const RATE_LIMITED = "You've sent this a few times already. Wait a minute, then try again."
export const ALREADY_SENT_HEADING = "This form has already been sent"
export const ALREADY_SENT_BODY =
  "Your clinician has it. Ask them if you need to change anything."

/**
 * A question this version of the portal cannot ask yet.
 *
 * Says what it is rather than apologising, and never claims the form is
 * finished — the server is what decides that, and it will refuse a form
 * that still needs this.
 */
export const ITEM_UNAVAILABLE = "This step will be available soon."

/** The demographics question. */
export const IDENTITY_HEADING = "Is this you?"
export const IDENTITY_CONFIRM = "Yes, that's me"
export const IDENTITY_DENY = "Something's not right"
export const IDENTITY_CORRECTIONS_LABEL = "What should we change? (optional)"
export const IDENTITY_CORRECTIONS_NOTE =
  "You can carry on either way — your clinician will read this and fix the record."
export const IDENTITY_NAME_LABEL = "Name"
export const IDENTITY_DOB_LABEL = "Date of birth"
export const IDENTITY_SUMMARY_CONFIRMED = "Confirmed"
export const IDENTITY_SUMMARY_FLAGGED = "Something to correct"
