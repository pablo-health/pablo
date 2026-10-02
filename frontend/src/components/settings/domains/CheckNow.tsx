// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"

interface CheckNowProps {
  checking: boolean
  /** Just clicked: the button rests, and says why. */
  resting: boolean
  checkedAt: Date | null
  disabled: boolean
  onCheck: () => void
}

/**
 * The Check now button and what it last did. A check answers in well under a
 * second, so without the line beside it a click looks like nothing happened.
 * It reports that the records were looked up, never that a domain works: the
 * rows' own status says that, once the server has made it so.
 */
export function CheckNow({ checking, resting, checkedAt, disabled, onCheck }: CheckNowProps) {
  const note = checking
    ? null
    : checkedAt && resting
      ? "Checked just now. You can check again in a moment."
      : checkedAt
        ? `Last checked at ${checkedAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.`
        : resting
          ? "You can check again in a moment."
          : "Looks up your DNS records."

  return (
    <div className="mb-3 flex flex-wrap items-center gap-3">
      <Button size="sm" variant="outline" disabled={disabled || checking || resting} onClick={onCheck}>
        {checking && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />}
        {checking ? "Checking…" : "Check now"}
      </Button>
      <span role="status" className="text-[12.5px] text-muted-foreground" data-testid="domains-check-note">
        {note}
      </span>
    </div>
  )
}
