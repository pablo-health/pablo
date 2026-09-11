// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Working-hours form for the (optional, last) onboarding step. The picker
 * itself is the shared `WorkingHoursGrid` — the calendar's first-run hours
 * step renders the same control. Saving creates one `working_hours`
 * availability rule per checked weekday, all sharing the single start/end
 * range — the same payload shape the settings surface's "seed from display
 * hours" action sends. Skipping creates no rules. Either path marks
 * onboarding_state "completed" and hands back to the wizard index.
 */

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import {
  DEFAULT_WORKING_HOURS,
  WorkingHoursGrid,
  isCompleteSelection,
  workingHoursRules,
  type WorkingHoursSelection,
} from "@/components/availability/WorkingHoursGrid"
import { useCreateAvailabilityRule } from "@/hooks/useAvailability"
import { updateUserProfile } from "@/lib/api/users"
import { trackOnboardingStepSkipped } from "@/lib/analytics/onboarding"

const GENERIC_ERROR = "Something went wrong. Please try again."

export function ScheduleStep() {
  const router = useRouter()
  const [selection, setSelection] = useState<WorkingHoursSelection>(DEFAULT_WORKING_HOURS)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const createRule = useCreateAvailabilityRule()

  const canSave = isCompleteSelection(selection)

  async function handleSave() {
    if (!canSave || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      for (const rule of workingHoursRules(selection)) {
        await createRule.mutateAsync(rule)
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
      <WorkingHoursGrid value={selection} onChange={setSelection} disabled={submitting} />

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
