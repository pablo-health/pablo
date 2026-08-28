// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Working-hours form for the (optional, last) onboarding step. Saving
 * creates one `working_hours` availability rule per checked weekday,
 * all sharing the single start/end range — the same payload shape the
 * settings surface's "seed from display hours" action sends. Skipping
 * creates no rules. Either path marks onboarding_state "completed" and
 * hands back to the wizard index.
 */

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useCreateAvailabilityRule } from "@/hooks/useAvailability"
import { updateUserProfile } from "@/lib/api/users"
import { trackOnboardingStepSkipped } from "@/lib/analytics/onboarding"

const WEEKDAYS = [
  { day_of_week: 0, label: "Monday" },
  { day_of_week: 1, label: "Tuesday" },
  { day_of_week: 2, label: "Wednesday" },
  { day_of_week: 3, label: "Thursday" },
  { day_of_week: 4, label: "Friday" },
  { day_of_week: 5, label: "Saturday" },
  { day_of_week: 6, label: "Sunday" },
] as const

const DEFAULT_SELECTED_DAYS = new Set([0, 1, 2, 3, 4])
const HOUR_OPTIONS = Array.from({ length: 24 }, (_, i) => i)

function formatHour(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`
}

const GENERIC_ERROR = "Something went wrong. Please try again."

export function ScheduleStep() {
  const router = useRouter()
  const [selectedDays, setSelectedDays] = useState<Set<number>>(DEFAULT_SELECTED_DAYS)
  const [startHour, setStartHour] = useState(9)
  const [endHour, setEndHour] = useState(17)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const createRule = useCreateAvailabilityRule()

  const canSave = selectedDays.size > 0 && endHour > startHour

  function toggleDay(day: number) {
    setSelectedDays((prev) => {
      const next = new Set(prev)
      if (next.has(day)) {
        next.delete(day)
      } else {
        next.add(day)
      }
      return next
    })
  }

  async function handleSave() {
    if (!canSave || submitting) return
    setSubmitting(true)
    setError(null)
    const start = formatHour(startHour)
    const end = formatHour(endHour)
    try {
      for (const { day_of_week } of WEEKDAYS) {
        if (!selectedDays.has(day_of_week)) continue
        await createRule.mutateAsync({
          rule_type: "working_hours",
          enforcement: "hard",
          params: { day_of_week, start, end },
        })
      }
      await updateUserProfile({ onboarding_state: "completed" })
      router.push("/onboarding")
    } catch {
      setError(GENERIC_ERROR)
      setSubmitting(false)
    }
  }

  async function handleSkip() {
    if (submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await updateUserProfile({ onboarding_state: "completed" })
      trackOnboardingStepSkipped("schedule")
      router.push("/onboarding")
    } catch {
      setError(GENERIC_ERROR)
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {WEEKDAYS.map(({ day_of_week, label }) => (
          <div key={day_of_week} className="flex items-center gap-2">
            <Checkbox
              id={`schedule-day-${day_of_week}`}
              checked={selectedDays.has(day_of_week)}
              onCheckedChange={() => toggleDay(day_of_week)}
              disabled={submitting}
            />
            <label
              htmlFor={`schedule-day-${day_of_week}`}
              className="text-sm text-neutral-700 cursor-pointer"
            >
              {label}
            </label>
          </div>
        ))}
      </div>

      <div className="flex items-end gap-4">
        <div className="grid gap-2">
          <Label htmlFor="schedule-start">Start</Label>
          <Select
            value={String(startHour)}
            onValueChange={(v) => setStartHour(Number(v))}
            disabled={submitting}
          >
            <SelectTrigger id="schedule-start" className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {HOUR_OPTIONS.map((h) => (
                <SelectItem key={h} value={String(h)}>
                  {formatHour(h)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <span className="pb-2 text-neutral-400">to</span>
        <div className="grid gap-2">
          <Label htmlFor="schedule-end">End</Label>
          <Select
            value={String(endHour)}
            onValueChange={(v) => setEndHour(Number(v))}
            disabled={submitting}
          >
            <SelectTrigger id="schedule-end" className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {HOUR_OPTIONS.map((h) => (
                <SelectItem key={h} value={String(h)}>
                  {formatHour(h)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {error && (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      )}

      <div className="flex items-center gap-4 pt-1">
        <Button type="button" onClick={handleSave} disabled={!canSave || submitting}>
          Save
        </Button>
        <button
          type="button"
          onClick={handleSkip}
          disabled={submitting}
          className="text-sm font-medium underline underline-offset-2"
          style={{ color: "var(--color-neutral-600)" }}
        >
          Skip for now
        </button>
      </div>
    </div>
  )
}
