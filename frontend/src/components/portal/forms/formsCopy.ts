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
export const LIST_CORRECTION = "Your practice has a question"

/**
 * What a row on the list is called.
 *
 * The title the practice wrote for the person filling it in, when it wrote
 * one. Never the packet's name: that is the name the clinician filed it
 * under in settings ("New client intake 2026-10-07"), written for their own
 * list, and the server does not send it here. With no title, the row says
 * whose forms these are.
 */
export function rowTitle(clientTitle: string | null, practiceName: string | null): string {
  const title = clientTitle?.trim()
  return title ? title : formsFrom(practiceName)
}

/** The row's title when the practice wrote none: whose forms these are. */
export function formsFrom(practiceName: string | null): string {
  return `Forms from ${practiceName ?? "your practice"}`
}

/** How much of a form is outstanding, from the count the server sent. */
export function questionsLeft(outstanding: number): string {
  return outstanding === 1 ? "1 question left" : `${outstanding} questions left`
}

/**
 * How much of a form in parts is outstanding, in the parts the walk shows.
 *
 * Parts rather than questions because the walk counts in parts ("Part 8 of
 * 14"), and a count of required questions leaves out every optional one the
 * walk still steps through — a row that said 30 opened a walk of 47.
 */
export function partsLeft(left: number, total: number): string {
  if (left === total) return `${total} parts`
  return `${left} of ${total} parts left`
}

/** Both dead ends a portal session can hand a form. */
export const EXPIRED_HEADING = "This link has expired"
export const EXPIRED_BODY = "You'll need a new sign-in link to carry on."
export const EXPIRED_ACTION = "Get a new sign-in link"

/** Everything else: worth another try. */
export const RETRY_HEADING = "Something went wrong"
export const RETRY_BODY = "We couldn't load your forms. Try again in a moment."
export const RETRY_ACTION = "Try again"

export const LOADING = "Loading your forms…"

/**
 * Where the patient is: which part of the form, and how far into it.
 *
 * A form's sections are its parts, and the count is kept to the part. "2 of
 * 3" under "Consent to telehealth" is a number somebody can hold in their
 * head; one count across the whole form reaches the dozens and reads as how
 * much is left to do, which is the screen people close.
 *
 * Only questions that collect something are counted. A paragraph to read is
 * not a question.
 */
export function questionPosition(index: number, total: number): string {
  return `Question ${index} of ${total}`
}

/** The count inside a named part, which already says what it is counting. */
export function positionInPart(index: number, total: number): string {
  return `${index} of ${total}`
}

export function partPosition(index: number, total: number): string {
  return `Part ${index} of ${total}`
}

/**
 * What a form's opening is called, when it is the identity check and the
 * reason for coming in and nothing else. Anything else that comes before the
 * first section gets no heading.
 */
export const OPENING_PART_TITLE = "About you"

/** What a part asks them to do, shown before its name: "Read and sign: Informed consent". */
export const PART_VERBS = {
  sign: "Read and sign",
  answer: "Answer",
  photo: "Send a photo",
  file: "Send a file",
} as const

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
 * A form the practice has sent back with a question about one answer.
 *
 * The heading says who is asking and the note under it is the practice's
 * own words, served with the form. Nothing here paraphrases the note or
 * explains why they might be asking — the clinician wrote the reason, and a
 * screen guessing at it would be guessing about somebody's care.
 *
 * It also does not recite what stays as it was. The rest of the form is not
 * on screen, which says that already; a sentence promising the other
 * answers are safe would introduce a worry nobody had.
 */
export const CORRECTION_HEADING = "Your clinician has asked about one thing"
export const CORRECTION_HEADING_MANY = "Your clinician has asked about a few things"
export const CORRECTION_SUBMIT = "Send this back"

export function correctionHeading(count: number): string {
  return count === 1 ? CORRECTION_HEADING : CORRECTION_HEADING_MANY
}

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

/**
 * The standard contact block: who to call, and how.
 *
 * Labelled in the same words the save route uses when it refuses one of
 * them, so "Their name is still blank." points at the box it came from
 * rather than at a field name nobody saw. The question above the three is
 * the practice's own wording, so there is no heading here.
 */
export const CONTACT_NAME_LABEL = "Their name"
export const CONTACT_RELATIONSHIP_LABEL = "How you know them"
export const CONTACT_PHONE_LABEL = "Their phone number"

/**
 * Signing a consent document.
 *
 * The screen says what typing a name does and stops. It does not say what
 * the signature is worth in law — that depends on where a practice operates
 * and on facts the product cannot check — and it does not recite what will
 * not happen to the document afterwards, which would introduce machinery
 * nobody had asked about.
 *
 * The consent statement itself is NOT here. It is served with the document,
 * because the version of it is recorded on every signature — a copy in the
 * front end would be free to drift from what a stored signature says was
 * agreed, and nothing would look wrong when it did.
 */
export const CONSENT_LOADING = "Loading this document…"
export const CONSENT_LOAD_FAILED = "We couldn't load this document. Try again in a moment."
export const CONSENT_NAME_LABEL = "Type your full name"
export const CONSENT_SIGN = "Sign"
export const CONSENT_SIGNING = "Signing…"
export const CONSENT_SIGNED_BADGE = "Signed"
/** A consent document drawn read-only with no signature on it. */
export const CONSENT_NOT_SIGNED = "Not signed."

/** Who a recorded signature was given as, on a read-only copy. */
export function consentSignerRole(role: string): string {
  return role === "guardian"
    ? "Signed by an adult completing it for the patient"
    : "Signed by the patient"
}
export const CONSENT_SIGN_FAILED = "We couldn't record that. Try again in a moment."

/** Who is signing, on a document that asks for more than one signature. */
export const CONSENT_ROLE_LABEL = "Who is signing?"
export const CONSENT_ROLE_PATIENT = "I'm signing for myself"
export const CONSENT_ROLE_GUARDIAN = "I'm signing for the patient"
export const CONSENT_GUARDIAN_NOTE =
  "If you are completing this for the patient, enter your own name."

/** Still outstanding on a document that asks for two signatures. */
export const CONSENT_AWAITING_GUARDIAN = "This document also needs a signature from an adult completing it for the patient."
export const CONSENT_AWAITING_PATIENT = "This document also needs the patient's own signature."

/**
 * A newer version has been published and this document asks for a fresh
 * signature. Says what to do rather than what went wrong: the practice sends
 * the new version, and nothing the patient typed was lost.
 */
export const CONSENT_NEEDS_RESIGN =
  "There's a newer version of this document. Your practice will send it to you to read and sign."

/** How a recorded signature reads back on the screen. */
export function consentSignedBy(name: string, signedAt: string): string {
  return `${name} — ${signedAt}`
}

/**
 * What the review screen calls a consent item when its document's title
 * cannot be read.
 *
 * The review row names a consent item by its document's own title, which it
 * reads from the server. When that read fails, or the item points at no
 * document, it says what kind of question it is rather than inventing a
 * heading the practice never wrote.
 */
export const CONSENT_REVIEW_LABEL = "Consent document"

/**
 * Sending in a file a form asked for.
 *
 * Says what to do and stops. It does not recite what happens to a photo
 * afterwards, or promise anything about how it will be checked — the file
 * goes to the practice, and describing the machinery between here and there
 * would introduce questions nobody had.
 *
 * The one refusal worth its own sentence is a file that is not the kind of
 * file it claims to be, because the fix is to send a different one.
 */
export const UPLOAD_CHOOSE = "Choose a file"
export const UPLOAD_TAKE_PHOTO = "Take a photo"
export const UPLOAD_SENDING = "Sending…"
export const UPLOAD_REMOVE = "Remove"
export const UPLOAD_SENT = "Sent"
export const UPLOAD_VIEW = "View"
/** A file slot drawn read-only with nothing in it. */
export const UPLOAD_NOT_SENT = "Not sent."
export const UPLOAD_FAILED_OPEN = "That file didn't open. Try again in a moment."
export const UPLOAD_FAILED = "We couldn't send that. Try again in a moment."
export const UPLOAD_WRONG_TYPE = "Send a PDF or a photo."
export const UPLOAD_TOO_LARGE = "That file is too big. Try a smaller one."

/** The two sides of an insurance card. */
export const CARD_FRONT = "Front of card"
export const CARD_BACK = "Back of card"

/** A practice that works from paper offers the form to print. */
export const BLANK_FORM_DOWNLOAD = "Download the form"
export const BLANK_FORM_NOTE =
  "Download it, fill it in, then take a photo or scan it and send it back here."

/** The review screen's one-liner for a question that asked for files. */
export function filesSent(count: number): string {
  return count === 1 ? "1 file sent" : `${count} files sent`
}

/**
 * The plan details a card question may also ask for.
 *
 * Optional on the screen and optional to the practice: a client who cannot
 * read a worn card still answers the question by photographing it. Nothing
 * here says what the practice will do with the plan, because at this point
 * nobody has checked anything with a payer.
 */
export const COVERAGE_HEADING = "What's on the card"
export const COVERAGE_NOTE = "If you can read these off the card, they help your practice."
export const COVERAGE_PAYER_LABEL = "Insurance company"
export const COVERAGE_MEMBER_LABEL = "Member ID"
export const COVERAGE_GROUP_LABEL = "Group number (optional)"
export const COVERAGE_SAVE = "Save these details"
export const COVERAGE_SAVING = "Saving…"
export const COVERAGE_SAVED = "Saved"
export const COVERAGE_FAILED = "We couldn't save those. Try again in a moment."

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
