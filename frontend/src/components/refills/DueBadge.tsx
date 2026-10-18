// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { dueLabel } from "@/lib/refillDue"

/** When the practice means to have answered a refill request, by its own target. */
export function DueBadge({ dueAt, timeZone }: { dueAt: string; timeZone: string }) {
  const { text, overdue } = dueLabel(dueAt, new Date(), timeZone)
  return (
    <span
      data-testid="refill-due"
      data-overdue={overdue}
      className={`block font-medium ${overdue ? "text-red-700" : "text-neutral-700"}`}
    >
      {text}
    </span>
  )
}
