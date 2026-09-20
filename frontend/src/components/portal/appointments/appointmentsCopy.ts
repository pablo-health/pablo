// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Every sentence the appointments module shows a patient, in one file so the
 * components and their tests cannot drift on wording.
 *
 * Two rules from `docs/reference/copy-style.md` shaped most of these:
 *
 * **Say only what the server has checked.** The module never tells a patient
 * they can book until the practice's policy says so, and never names a phone
 * number the practice has not given.
 *
 * **Don't recite what will not happen.** The late-change warning is the
 * engine's own sentence rather than one written here, because it is the only
 * thing standing between a patient and a fee nobody mentioned — and because a
 * client that reworded it would be reciting a policy it has not read.
 */

/** Nothing to show yet. Not a problem, so it does not read as one. */
export const NO_UPCOMING = "No appointments coming up."

export const UPCOMING_HEADING = "Upcoming"
export const PAST_HEADING = "Earlier"

/** The button that starts the booking journey. */
export const REQUEST_APPOINTMENT = "Request an appointment"

/**
 * Shown instead of a booking button when the practice does not take online
 * bookings. It says what is true and what to do about it, and stops there —
 * a disabled button with no sentence beside it is the failure this replaces.
 */
export const BOOKING_OFF = "This practice doesn't take bookings online."

/** Appended to {@link BOOKING_OFF} when the practice published a number. */
export function callToBook(phone: string): string {
  return `Call ${phone} to book or change an appointment.`
}

/**
 * When the practice has given no number. Messaging is the channel the portal
 * itself can offer, and a patient of a practice running one has it; naming it
 * is better than naming nothing.
 */
export const CONTACT_TO_BOOK = "Contact your practice to book or change an appointment."

/**
 * The practice turned booking on and has opened no appointment type. Rare,
 * and worth its own sentence: offering an empty picker would read as a bug,
 * and saying "booking is off" would be false.
 */
export const NOTHING_BOOKABLE = "There's nothing available to book online right now."

export const PENDING_NOTICE = "Waiting for the practice to confirm"

export const SLOT_TAKEN = "That time was just taken. Here's what's open now."

export const NO_SLOTS = "Nothing open that day."

export const BOOKED_HEADLINE = "You're booked"

export const RESCHEDULED_HEADLINE = "You're rescheduled"

export const REQUEST_SENT_HEADLINE = "Request sent"

export const REQUEST_SENT_EXPECTATION =
  "Your practice will confirm it shortly. You'll see it here once they have."

export const BACK_TO_APPOINTMENTS = "Back to your appointments"

export const LOAD_FAILED = "Your appointments aren't loading right now. Try again in a moment."

export const SLOTS_FAILED = "Times aren't loading right now. Try again in a moment."

export const ACTION_FAILED = "That didn't go through. Try again."

export const JOIN = "Join"

export const RESCHEDULE = "Reschedule"

export const CANCEL = "Cancel"

/** The patient's answer to the engine's late-change warning. */
export const CONFIRM_LATE_CHANGE = "Go ahead"

export const KEEP_APPOINTMENT = "Keep it"
