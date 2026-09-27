// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * The optional onboarding question "Are you importing from another EHR?".
 *
 * Asked once. Every path stamps `import_prompted_at` (the step's gate), so
 * the wizard moves on and never asks again:
 *
 * - SimplePractice records the answer and opens the import screen, where the
 *   practice uploads its export. The import runs in the background; nothing
 *   here waits on it.
 * - Another system records the answer and continues setup. There is no
 *   importer for it yet, and the screen does not say so — it just moves on.
 * - Skip records only that the question was asked.
 *
 * The component takes no props so a downstream onboarding surface can mount
 * it inside its own shell unchanged.
 */

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { OptionCards, type CardOption } from "@/components/settings/ui"
import { updateUserProfile, type ImportSource } from "@/lib/api/users"
import { trackOnboardingStepSkipped } from "@/lib/analytics/onboarding"

const GENERIC_ERROR = "Something went wrong. Please try again."

type Choice = Exclude<ImportSource, "none">

const OPTIONS: CardOption<Choice>[] = [
  {
    value: "simplepractice",
    label: "SimplePractice",
    hint: "Upload your SimplePractice export and bring your clients and notes across.",
  },
  {
    value: "other",
    label: "Another system",
    hint: "Carry on with setup. You can add your clients yourself.",
  },
]

/** Where the import screen lives; onboarding hands a SimplePractice practice there. */
export const IMPORT_SCREEN_PATH = "/dashboard/settings/import"

export function ImportSourceStep() {
  const router = useRouter()
  const [choice, setChoice] = useState<Choice>("simplepractice")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function record(next: () => void, body: Parameters<typeof updateUserProfile>[0]) {
    if (submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await updateUserProfile(body)
      next()
    } catch {
      setError(GENERIC_ERROR)
      setSubmitting(false)
    }
  }

  function handleContinue() {
    void record(
      () => router.push(choice === "simplepractice" ? IMPORT_SCREEN_PATH : "/onboarding"),
      { import_source: choice },
    )
  }

  function handleSkip() {
    void record(
      () => {
        trackOnboardingStepSkipped("import-source")
        router.push("/onboarding")
      },
      { import_prompted: true },
    )
  }

  return (
    <div className="space-y-6">
      <OptionCards
        value={choice}
        onChange={setChoice}
        options={OPTIONS}
        label="Where your records are now"
        columns={2}
      />

      {error && (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      )}

      <div className="flex items-center gap-4 pt-1">
        <Button type="button" onClick={handleContinue} disabled={submitting}>
          Continue
        </Button>
        <button
          type="button"
          onClick={handleSkip}
          disabled={submitting}
          className="text-sm font-medium underline underline-offset-2"
          style={{ color: "var(--color-neutral-600)" }}
        >
          Skip
        </button>
      </div>
    </div>
  )
}
