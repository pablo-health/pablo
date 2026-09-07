// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * How a claim reads on screen: the badge for each state, the next thing a
 * person does about it, and the deadline that binds it.
 *
 * One table, so the tracker row, the queue row and the detail view all say
 * the same thing about the same claim. The copy never says "Sent" before
 * the clearinghouse has taken the claim: a `validated` claim is queued and
 * has not left the practice, so it reads "Queued to send"; `submitted` is
 * stamped only when the clearinghouse accepted the upload.
 *
 * What to do next is keyed by the API's `next_action`, never derived from
 * the state here — the pipeline knows things the state alone does not, such
 * as a queued claim whose filing attempt is already in flight.
 */

import type {
  ClaimDeadlines,
  ClaimState,
  DeadlineKind,
  FrequencyCode,
  NextAction,
} from "@/types/claims"

export type BadgeTone = "neutral" | "info" | "success" | "warning" | "danger"

export interface StatePresentation {
  label: string
  tone: BadgeTone
  /** The claim needs attention beyond waiting. */
  alert: boolean
}

const STATES: Record<ClaimState, StatePresentation> = {
  draft: { label: "Draft", tone: "neutral", alert: false },
  validated: { label: "Queued to send", tone: "info", alert: false },
  submitted: { label: "Sent", tone: "info", alert: false },
  ch_accepted: { label: "Accepted by clearinghouse", tone: "info", alert: false },
  payer_accepted: { label: "Accepted by payer", tone: "info", alert: false },
  paid: { label: "Paid", tone: "success", alert: false },
  partial: { label: "Partially paid", tone: "warning", alert: true },
  denied: { label: "Denied", tone: "danger", alert: true },
  rejected: { label: "Rejected", tone: "danger", alert: true },
  stalled: { label: "Needs attention", tone: "warning", alert: true },
}

export function presentState(state: ClaimState): StatePresentation {
  return STATES[state]
}

const NEXT_ACTIONS: Record<NextAction, string> = {
  review_and_file: "Review and file",
  queued_to_send: "Queued to send",
  sending: "Sending",
  await_acknowledgment: "Waiting for the clearinghouse to acknowledge it",
  await_payer: "Waiting for the payer to accept it",
  await_remittance: "Waiting for the remittance",
  review_remittance: "Review the remittance; correct or appeal",
  correct_and_resubmit: "Fix and refile",
  appeal_or_correct: "Correct and resubmit, or appeal",
  check_with_clearinghouse: "No receipt in time; check with the clearinghouse",
}

/** What a person does next, or `null` on a paid claim, which needs nothing. */
export function presentNextAction(nextAction: NextAction | null): string | null {
  return nextAction === null ? null : NEXT_ACTIONS[nextAction]
}

/** What kind of claim this row is, when it is not an original. */
export function frequencyLabel(code: FrequencyCode): string | null {
  if (code === "7") return "Corrected claim"
  if (code === "8") return "Void"
  return null
}

/** The claim can be corrected or voided: it has left the practice, and is not itself a void. */
export function canCorrectOrVoid(state: ClaimState, frequencyCode: FrequencyCode): boolean {
  return state !== "draft" && state !== "validated" && frequencyCode !== "8"
}

/** A draft is the only claim that is reviewed and filed from here. */
export function canReviewAndFile(state: ClaimState): boolean {
  return state === "draft"
}

/** Whole days between an instant and now, never negative. */
export function ageInDays(since: string, now: Date = new Date()): number {
  const elapsed = now.getTime() - new Date(since).getTime()
  return Math.max(0, Math.floor(elapsed / 86_400_000))
}

/** Render an ISO calendar date (`YYYY-MM-DD`) without a timezone shift. */
export function formatIsoDate(isoDate: string): string {
  const [year, month, day] = isoDate.split("-").map(Number)
  return new Date(year, month - 1, day).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

/** Amber this close to the deadline; red this close or once it has passed. */
export const DEADLINE_AMBER_DAYS = 14
export const DEADLINE_RED_DAYS = 2

export interface DeadlinePresentation {
  kind: DeadlineKind
  /** ISO calendar date. */
  date: string
  daysLeft: number
  tone: BadgeTone
  text: string
}

const DEADLINE_LABELS: Record<DeadlineKind, string> = {
  filing: "Filing closes",
  correction: "Correction window closes",
  appeal: "Appeal window closes",
}

/**
 * The deadline that binds the claim, ready to render, or `null` when none
 * does. A rejected claim's deadline reads as the instruction it is.
 */
export function presentDeadline(
  deadlines: ClaimDeadlines,
  state: ClaimState,
): DeadlinePresentation | null {
  const kind = deadlines.applicable
  if (kind === null) return null
  const date = deadlines[kind]
  const daysLeft = deadlines.days_left
  if (date === null || daysLeft === null) return null

  const tone: BadgeTone =
    daysLeft <= DEADLINE_RED_DAYS ? "danger" : daysLeft <= DEADLINE_AMBER_DAYS ? "warning" : "neutral"
  const when = formatIsoDate(date)
  const remaining =
    daysLeft < 0
      ? `passed ${-daysLeft} ${-daysLeft === 1 ? "day" : "days"} ago`
      : `${daysLeft} ${daysLeft === 1 ? "day" : "days"}`
  const text =
    state === "rejected"
      ? `Fix and refile before ${when} (${remaining})`
      : `${DEADLINE_LABELS[kind]} ${when} (${remaining})`
  return { kind, date, daysLeft, tone, text }
}

export const TONE_CLASSES: Record<BadgeTone, string> = {
  neutral: "bg-neutral-100 text-neutral-700",
  info: "bg-sky-100 text-sky-800",
  success: "bg-emerald-100 text-emerald-800",
  warning: "bg-amber-100 text-amber-800",
  danger: "bg-red-100 text-red-800",
}
