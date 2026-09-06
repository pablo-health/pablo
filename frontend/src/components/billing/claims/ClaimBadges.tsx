// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The two badges every claim surface shows: where the claim stands, and the
 * deadline that binds it. Both read from `claimPresentation` so the tracker,
 * the queue and the detail view never disagree.
 */

"use client"

import { cn } from "@/lib/utils"
import type { ClaimDeadlines, ClaimState } from "@/types/claims"
import { presentDeadline, presentState, TONE_CLASSES } from "./claimPresentation"

interface ClaimStateBadgeProps {
  state: ClaimState
  className?: string
}

export function ClaimStateBadge({ state, className }: ClaimStateBadgeProps) {
  const { label, tone } = presentState(state)
  return (
    <span
      data-testid="claim-state"
      data-state={state}
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        TONE_CLASSES[tone],
        className,
      )}
    >
      {label}
    </span>
  )
}

interface DeadlineBadgeProps {
  deadlines: ClaimDeadlines
  state: ClaimState
  className?: string
}

/** Renders nothing for a claim under no clock. */
export function DeadlineBadge({ deadlines, state, className }: DeadlineBadgeProps) {
  const deadline = presentDeadline(deadlines, state)
  if (deadline === null) return null
  return (
    <span
      data-testid="claim-deadline"
      data-tone={deadline.tone}
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        TONE_CLASSES[deadline.tone],
        className,
      )}
    >
      {deadline.text}
    </span>
  )
}
