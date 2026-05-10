// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { EditorialView } from "./dateUtils"

interface EditorialViewSwitcherProps {
  view: EditorialView
  onChange: (next: EditorialView) => void
}

const VIEWS: { value: EditorialView; label: string }[] = [
  { value: "day", label: "Day" },
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
]

export function EditorialViewSwitcher({ view, onChange }: EditorialViewSwitcherProps) {
  return (
    <div role="tablist" aria-label="Calendar view" className="flex items-center gap-6 text-sm">
      {VIEWS.map((v) => (
        <button
          key={v.value}
          type="button"
          role="tab"
          aria-selected={view === v.value}
          data-active={view === v.value}
          onClick={() => onChange(v.value)}
          className="ed-tab font-medium tracking-wide outline-none focus-visible:opacity-80"
        >
          {v.label}
        </button>
      ))}
    </div>
  )
}
