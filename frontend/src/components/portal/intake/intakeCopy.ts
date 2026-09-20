// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Every sentence the intake form shows a patient, in one file.
 *
 * Collected here because this is safety copy as much as it is UI copy, and
 * reviewing it should not mean reading four components. The item wording
 * itself is NOT here — that comes from the server, so the form and the
 * scorer cannot drift.
 */

/**
 * Shown on the depression screener and again when the form is done.
 *
 * Always, never conditionally. Showing it only when someone answers a
 * particular item a particular way would imply that answers are being read
 * as they are typed, and they are not: the form is filled in, submitted, and
 * read by a clinician afterwards. A crisis line that appears in response to
 * an answer promises a monitor that does not exist.
 */
export const CRISIS_FOOTER =
  "If you're in crisis or thinking about harming yourself, call or text 988 (Suicide & Crisis Lifeline), or call 911 in an emergency."

/** The done screen. No totals, no severity: reading a screener is clinical work. */
export const DONE_HEADING = "All done"
export const DONE_BODY = "Thanks — your responses have been sent to your clinician."

/** Both dead ends a portal session can hand this form. */
export const EXPIRED_HEADING = "This link has expired"
export const EXPIRED_BODY =
  "Ask your clinician for a new invite link, then start again."

/** Everything else: worth another try. */
export const RETRY_HEADING = "Something went wrong"
export const RETRY_BODY = "We couldn't load your form. Try again in a moment."
export const RETRY_ACTION = "Try again"

export const SUBMIT_FAILED = "We couldn't send your answers. Try again in a moment."
export const SUBMIT_RATE_LIMITED = "You've sent this a few times already. Wait a minute, then try again."
export const SUBMIT_REJECTED = "Something in the form didn't look right. Check your answers and try again."

/** Identity step. */
export const IDENTITY_HEADING = "Is this you?"
export const IDENTITY_CONFIRM = "Yes, that's me"
export const IDENTITY_DENY = "Something's not right"
export const IDENTITY_CORRECTIONS_LABEL = "What should we change? (optional)"
export const IDENTITY_CORRECTIONS_NOTE =
  "You can carry on either way — your clinician will read this and fix the record."

/** Navigation. */
export const BACK = "Back"
export const CONTINUE = "Continue"
export const SUBMIT = "Send to my clinician"
export const SUBMITTING = "Sending…"
export const LOADING = "Loading your form…"
