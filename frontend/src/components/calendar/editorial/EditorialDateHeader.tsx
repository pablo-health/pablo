// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { ChevronLeft, ChevronRight } from "lucide-react"
import type { EditorialView } from "./dateUtils"
import { rangeLabel } from "./dateUtils"

interface EditorialDateHeaderProps {
  view: EditorialView
  anchor: Date
  onPrev: () => void
  onNext: () => void
  onToday: () => void
  onPickerOpen?: () => void
  /** `summarize()` of the rule that blanks the whole displayed day, e.g.
   * "Friday blocked" — set only when `view === "day"` and the anchor date
   * is fully unavailable per a whole-day-blocking rule. */
  blockedLabel?: string
}

export function EditorialDateHeader({
  view,
  anchor,
  onPrev,
  onNext,
  onToday,
  onPickerOpen,
  blockedLabel,
}: EditorialDateHeaderProps) {
  const { primary, secondary } = rangeLabel(view, anchor)

  return (
    <div className="flex flex-wrap items-center gap-[14px]">
      <button
        type="button"
        onClick={onPickerOpen}
        className="group flex min-w-0 items-baseline gap-2 text-left"
        aria-label="Jump to a date"
      >
        <h2
          className="font-display text-[25px] font-semibold leading-[1.05] tracking-[-0.01em]"
          style={{ color: "var(--ed-ink)" }}
        >
          {primary}
        </h2>
        {secondary && (
          <span
            className="font-display text-[19px] font-light tracking-[-0.01em]"
            style={{ color: "var(--ed-ink-soft)" }}
          >
            {secondary}
          </span>
        )}
      </button>
      {blockedLabel && (
        <span
          className="rounded-full px-2.5 py-1 text-[11px] font-semibold tracking-wide"
          style={{
            backgroundColor: "var(--ed-hairline-strong)",
            color: "var(--ed-ink-muted)",
          }}
        >
          {blockedLabel}
        </span>
      )}
      <div className="flex items-center gap-0.5">
        <button
          type="button"
          onClick={onPrev}
          className="rounded-full p-[7px] transition-colors hover:bg-[var(--ed-pill-hover)]"
          style={{ color: "var(--ed-ink)" }}
          aria-label="Previous"
        >
          <ChevronLeft className="h-[18px] w-[18px]" />
        </button>
        <button
          type="button"
          onClick={onNext}
          className="rounded-full p-[7px] transition-colors hover:bg-[var(--ed-pill-hover)]"
          style={{ color: "var(--ed-ink)" }}
          aria-label="Next"
        >
          <ChevronRight className="h-[18px] w-[18px]" />
        </button>
        <button
          type="button"
          onClick={onToday}
          className="ml-1 rounded-full px-[15px] py-1.5 text-[13.5px] font-medium transition-colors hover:bg-[var(--ed-pill-hover)]"
          style={{ color: "var(--ed-ink)" }}
        >
          Today
        </button>
      </div>
    </div>
  )
}
