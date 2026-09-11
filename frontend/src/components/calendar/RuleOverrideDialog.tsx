// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Image from "next/image"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import type { ConflictResponse, RuleType } from "@/types/availability"

/**
 * How a crossed rule is described back to the therapist, in the vocabulary
 * the availability settings page uses (see `summarize` there — one rule at
 * a time; these are the same rules read as reasons).
 *
 * Enforcement changes the framing, not the mechanics: a hard rule is a
 * boundary the therapist drew, a soft one is a habit they keep.
 */
const HARD_REASONS: Record<RuleType, string> = {
  block_specific_dates: "you've blocked that date",
  block_date_range: "you've blocked that stretch of dates",
  block_day_of_week: "you've blocked that day of the week",
  block_time_range: "you've blocked that time of day",
  working_hours: "it's outside your working hours",
  max_per_day: "it's past your limit for one day",
  buffer_before: "it doesn't leave the gap you keep before an appointment",
  buffer_after: "it doesn't leave the gap you keep after an appointment",
  session_defaults: "it doesn't match your session defaults",
}

const SOFT_REASONS: Record<RuleType, string> = {
  block_specific_dates: "you usually don't book that date",
  block_date_range: "you usually don't book that stretch of dates",
  block_day_of_week: "you usually don't book that day of the week",
  block_time_range: "you usually don't book at that time of day",
  working_hours: "it's outside the hours you usually work",
  max_per_day: "it's more than you usually book in a day",
  buffer_before: "it leaves less than your usual gap before it",
  buffer_after: "it leaves less than your usual gap after it",
  session_defaults: "it doesn't match your session defaults",
}

// Most specific reason first, so the sentence leads with the rule that
// explains the most: a blocked date says more than a daily cap does.
const SPECIFICITY: RuleType[] = [
  "block_specific_dates",
  "block_date_range",
  "block_day_of_week",
  "block_time_range",
  "working_hours",
  "max_per_day",
  "buffer_before",
  "buffer_after",
  "session_defaults",
]

// Past this many the sentence stops being a sentence, so the remainder is
// counted instead. Every rule is still listed in full under the details.
const MAX_REASONS_IN_SENTENCE = 3

function reasonFor(conflict: ConflictResponse): string {
  const table = conflict.enforcement === "hard" ? HARD_REASONS : SOFT_REASONS
  const reason: string | undefined = table[conflict.rule_type]
  if (reason) return reason
  return conflict.enforcement === "hard"
    ? "you've blocked this time"
    : "you usually don't book this time"
}

function joinReasons(reasons: string[]): string {
  if (reasons.length <= 1) return reasons[0] ?? ""
  if (reasons.length === 2) return `${reasons[0]} and ${reasons[1]}`
  return `${reasons.slice(0, -1).join(", ")}, and ${reasons[reasons.length - 1]}`
}

/**
 * One readable sentence for however many rules a window runs into.
 *
 * A single window commonly trips several rules at once — a blocked weekday,
 * a per-day cap and a buffer all at the same time. Listing them verbatim
 * reads like an export of the settings page, so the strongest and most
 * specific ones lead and the rest are counted.
 */
export function summarizeConflicts(conflicts: ConflictResponse[]): string {
  const ordered = [...conflicts].sort((a, b) => {
    if (a.enforcement !== b.enforcement) return a.enforcement === "hard" ? -1 : 1
    return SPECIFICITY.indexOf(a.rule_type) - SPECIFICITY.indexOf(b.rule_type)
  })
  // Two rules of the same kind read as one reason, not as a repetition.
  const reasons = [...new Set(ordered.map(reasonFor))]
  if (reasons.length === 0) return ""

  const leading = reasons.slice(0, MAX_REASONS_IN_SENTENCE)
  const remaining = reasons.length - leading.length
  let sentence = joinReasons(leading)
  if (remaining > 0) {
    sentence += ` — and ${remaining} more ${remaining === 1 ? "reason" : "reasons"}`
  }
  return `${sentence.charAt(0).toUpperCase()}${sentence.slice(1)}.`
}

interface RuleOverrideDialogProps {
  open: boolean
  conflicts: ConflictResponse[]
  /** A series is booked all at once, so the question is asked about all of it. */
  recurring?: boolean
  onOverride: () => void
  onCancel: () => void
}

/**
 * Asks whether to book into a window the therapist's own availability rules
 * cover, rather than refusing it.
 *
 * The dialog states the reason and overrides one booking. It never touches a
 * rule: there is no unblock, no disable, no delete and no link that edits
 * one. Changing a rule is a settings decision, made in settings, and it
 * shouldn't take deleting a Friday block to get one Friday appointment on
 * the calendar.
 */
export function RuleOverrideDialog({
  open,
  conflicts,
  recurring = false,
  onOverride,
  onCancel,
}: RuleOverrideDialogProps) {
  const subject = recurring ? "series" : "event"

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onCancel()}>
      <DialogContent className="max-w-sm text-center">
        <div className="flex justify-center pt-2">
          <Image src="/pablo-today.webp" alt="Pablo bear" width={72} height={72} />
        </div>
        <DialogHeader className="items-center">
          <DialogTitle className="font-display">Book this {subject} anyway?</DialogTitle>
          <DialogDescription className="text-center">
            {recurring
              ? `This series runs into your availability rules. ${summarizeConflicts(conflicts)}`
              : summarizeConflicts(conflicts)}
          </DialogDescription>
        </DialogHeader>
        <p className="text-sm text-neutral-600">
          Do you want to override this {subject}?
        </p>

        {conflicts.length > 0 && (
          <details className="text-left text-xs text-neutral-500">
            <summary className="cursor-pointer select-none">
              {conflicts.length === 1 ? "The rule in full" : "All the rules in full"}
            </summary>
            <ul className="mt-2 flex flex-col gap-1 pl-4">
              {conflicts.map((conflict, index) => (
                <li key={`${conflict.rule_type}-${index}`} className="list-disc">
                  {conflict.message}
                  <span className="opacity-70">
                    {" · "}
                    {conflict.enforcement === "hard"
                      ? "Always enforced"
                      : "Warns, still bookable"}
                  </span>
                </li>
              ))}
            </ul>
          </details>
        )}

        <div className="flex flex-col gap-2 pt-1">
          <Button onClick={onOverride}>Override this {subject}</Button>
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
