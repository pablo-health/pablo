// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Days, times of day and format: the scheduling value of a preference, in the
 * waitlist's words and choices.
 */

"use client"

import { useId } from "react"
import {
  DAYS_LEGEND,
  DAYS_REQUIRED,
  DAY_LABELS,
  DAY_PART_LABELS,
  MODALITY_CHOICES,
  MODALITY_LEGEND,
  TIMES_LEGEND,
  TIMES_REQUIRED,
} from "@/components/waitlist/waitlistCopy"
import type { ScheduleWindow } from "@/types/patientPreferences"
import { DAY_PARTS, WEEKDAYS } from "@/types/waitlist"

function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}

/** What stops a window being saved, by field. */
export function windowProblems(value: ScheduleWindow): ("days" | "times")[] {
  return [
    ...(value.days.length === 0 ? (["days"] as const) : []),
    ...(value.times.length === 0 ? (["times"] as const) : []),
  ]
}

interface ScheduleWindowFieldsProps {
  value: ScheduleWindow
  onChange: (value: ScheduleWindow) => void
  showErrors: boolean
}

export function ScheduleWindowFields({ value, onChange, showErrors }: ScheduleWindowFieldsProps) {
  const ids = useId()
  const problems = showErrors ? windowProblems(value) : []

  return (
    <>
      <fieldset aria-describedby={problems.includes("days") ? `${ids}-days-error` : undefined}>
        <legend className="mb-1.5 text-sm font-medium text-neutral-900">{DAYS_LEGEND}</legend>
        <div className="flex flex-wrap gap-x-3 gap-y-1.5">
          {WEEKDAYS.map((day) => (
            <label key={day} className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={value.days.includes(day)}
                onChange={() => onChange({ ...value, days: toggle(value.days, day) })}
              />
              {DAY_LABELS[day]}
            </label>
          ))}
        </div>
        {problems.includes("days") && (
          <p id={`${ids}-days-error`} className="mt-1 text-xs text-red-600">
            {DAYS_REQUIRED}
          </p>
        )}
      </fieldset>

      <fieldset aria-describedby={problems.includes("times") ? `${ids}-times-error` : undefined}>
        <legend className="mb-1.5 text-sm font-medium text-neutral-900">{TIMES_LEGEND}</legend>
        <div className="flex flex-wrap gap-x-3 gap-y-1.5">
          {DAY_PARTS.map((part) => (
            <label key={part} className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={value.times.includes(part)}
                onChange={() => onChange({ ...value, times: toggle(value.times, part) })}
              />
              {DAY_PART_LABELS[part]}
            </label>
          ))}
        </div>
        {problems.includes("times") && (
          <p id={`${ids}-times-error`} className="mt-1 text-xs text-red-600">
            {TIMES_REQUIRED}
          </p>
        )}
      </fieldset>

      <fieldset>
        <legend className="mb-1.5 text-sm font-medium text-neutral-900">{MODALITY_LEGEND}</legend>
        <div className="flex flex-wrap gap-x-3 gap-y-1.5">
          {MODALITY_CHOICES.map((choice) => (
            <label key={choice.value} className="flex items-center gap-1.5 text-sm">
              <input
                type="radio"
                name={`${ids}-modality`}
                checked={value.modality === choice.value}
                onChange={() => onChange({ ...value, modality: choice.value })}
              />
              {choice.label}
            </label>
          ))}
        </div>
      </fieldset>
    </>
  )
}
