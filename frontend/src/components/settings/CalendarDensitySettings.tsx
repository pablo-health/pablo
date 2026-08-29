// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { UserPreferences } from "@/lib/api/users"

interface CalendarDensitySettingsProps {
  preferences: UserPreferences
  onSave: (prefs: UserPreferences) => void
  isSaving: boolean
}

const DENSITIES: { value: UserPreferences["calendar_density"]; label: string; hint: string }[] = [
  { value: "gentle", label: "Gentle", hint: "Tall rows with generous breathing room" },
  { value: "balanced", label: "Balanced", hint: "The default calendar layout" },
  { value: "compact", label: "Compact", hint: "Short rows that fit more of the day on screen" },
]

export function CalendarDensitySettings({ preferences, onSave, isSaving }: CalendarDensitySettingsProps) {
  return (
    <div
      role="radiogroup"
      aria-label="Calendar density"
      className="grid grid-cols-1 gap-2 sm:grid-cols-3"
    >
      {DENSITIES.map((d) => {
        const active = d.value === preferences.calendar_density
        return (
          <button
            key={d.value}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={isSaving}
            onClick={() => onSave({ ...preferences, calendar_density: d.value })}
            className={`rounded-lg border p-3 text-left transition-colors ${
              active
                ? "border-neutral-900 bg-neutral-900 text-white"
                : "border-neutral-300 text-neutral-700 hover:bg-neutral-100"
            }`}
          >
            <div className="text-sm font-medium">{d.label}</div>
            <div className={`text-xs ${active ? "text-neutral-300" : "text-neutral-500"}`}>{d.hint}</div>
          </button>
        )
      })}
    </div>
  )
}
