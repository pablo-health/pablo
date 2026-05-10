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
}

export function EditorialDateHeader({
  view,
  anchor,
  onPrev,
  onNext,
  onToday,
  onPickerOpen,
}: EditorialDateHeaderProps) {
  const { primary, secondary } = rangeLabel(view, anchor)

  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <button
        type="button"
        onClick={onPickerOpen}
        className="group flex items-baseline gap-2 text-left"
        aria-label="Jump to a date"
      >
        <h2
          className="font-display text-[24px] font-semibold leading-[1.1] tracking-[-0.01em] sm:text-[28px]"
          style={{ color: "var(--ed-ink)" }}
        >
          {primary}
        </h2>
        <span
          className="font-display text-[20px] font-light tracking-tight sm:text-[22px]"
          style={{ color: "var(--ed-ink-soft)" }}
        >
          {secondary}
        </span>
      </button>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={onPrev}
          className="rounded-full p-2 transition-colors hover:bg-[var(--ed-pill-hover)]"
          style={{ color: "var(--ed-ink)" }}
          aria-label="Previous"
        >
          <ChevronLeft className="h-5 w-5" />
        </button>
        <button
          type="button"
          onClick={onNext}
          className="rounded-full p-2 transition-colors hover:bg-[var(--ed-pill-hover)]"
          style={{ color: "var(--ed-ink)" }}
          aria-label="Next"
        >
          <ChevronRight className="h-5 w-5" />
        </button>
        <button
          type="button"
          onClick={onToday}
          className="ml-2 rounded-full px-4 py-1.5 text-sm font-medium transition-colors hover:bg-[var(--ed-pill-hover)]"
          style={{ color: "var(--ed-ink)" }}
        >
          Today
        </button>
      </div>
    </div>
  )
}
