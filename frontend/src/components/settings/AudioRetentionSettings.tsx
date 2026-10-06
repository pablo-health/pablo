// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useAudioRetention, useAudioRetentionSetting } from "@/hooks/useAudioRetention"
import { ApiError } from "@/lib/api/client"
import {
  AUDIO_RETENTION_DEFAULT_DAYS,
  AUDIO_RETENTION_MAX_DAYS,
  AUDIO_RETENTION_MIN_DAYS,
  AUDIO_RETENTION_ON_SIGNING,
} from "@/lib/api/practices"

type Choice = "on_signing" | "days"

/**
 * When the practice deletes session audio: once the note is signed, or a
 * number of days after the session.
 *
 * Deleting on signing waits for the signature because redrafting a note and
 * dictating more both use the recording; the backend deletes it in the request
 * that signs (app.services.audio_retention).
 */
export function AudioRetentionSettings() {
  const { data, error, isLoading } = useAudioRetentionSetting()
  if (error instanceof ApiError && error.status === 403) {
    return (
      <p className="text-sm text-neutral-600">
        Only the practice owner can change this.
      </p>
    )
  }
  if (isLoading || !data) return null
  return <RetentionForm savedDays={data.audio_retention_days} />
}

function RetentionForm({ savedDays: initialSaved }: { savedDays: number }) {
  const [savedDays, setSavedDays] = useState(initialSaved)
  const [choice, setChoice] = useState<Choice>(
    initialSaved === AUDIO_RETENTION_ON_SIGNING ? "on_signing" : "days",
  )
  const [daysText, setDaysText] = useState(
    String(
      initialSaved === AUDIO_RETENTION_ON_SIGNING ? AUDIO_RETENTION_DEFAULT_DAYS : initialSaved,
    ),
  )
  const [showSaved, setShowSaved] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const mutation = useAudioRetention()

  const days = choice === "on_signing" ? AUDIO_RETENTION_ON_SIGNING : Number(daysText)
  const daysValid =
    choice === "on_signing" ||
    (Number.isInteger(days) && days >= AUDIO_RETENTION_MIN_DAYS && days <= AUDIO_RETENTION_MAX_DAYS)
  const isDirty = days !== savedDays
  const isSaving = mutation.isPending

  const handleSave = () => {
    setErrorMessage(null)
    mutation.mutate(
      { days },
      {
        onSuccess: (saved) => {
          setSavedDays(saved.audio_retention_days)
          setShowSaved(true)
          window.setTimeout(() => setShowSaved(false), 2000)
        },
        onError: (err) => {
          setErrorMessage(
            err instanceof Error ? err.message : "Couldn't save the audio setting.",
          )
        },
      },
    )
  }

  return (
    <div className="space-y-4">
      <fieldset className="space-y-3" disabled={isSaving}>
        <legend className="sr-only">When session audio is deleted</legend>
        <label className="flex items-center gap-2 text-sm text-neutral-900">
          <input
            type="radio"
            name="audio-retention"
            checked={choice === "on_signing"}
            onChange={() => setChoice("on_signing")}
            className="accent-primary"
          />
          Delete when the note is signed
        </label>
        <div className="flex flex-wrap items-center gap-2 text-sm text-neutral-900">
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="audio-retention"
              checked={choice === "days"}
              onChange={() => setChoice("days")}
              className="accent-primary"
            />
            Delete after
          </label>
          <Input
            type="number"
            inputMode="numeric"
            aria-label="Days to keep session audio"
            min={AUDIO_RETENTION_MIN_DAYS}
            max={AUDIO_RETENTION_MAX_DAYS}
            value={daysText}
            onChange={(e) => {
              setDaysText(e.target.value)
              setChoice("days")
            }}
            className="w-24"
          />
          <span>days after the session</span>
        </div>
      </fieldset>

      {!daysValid && (
        <p className="text-sm text-red-600" role="alert">
          Enter a number of days from {AUDIO_RETENTION_MIN_DAYS} to {AUDIO_RETENTION_MAX_DAYS}.
        </p>
      )}

      <div className="flex items-center gap-3">
        <Button size="sm" onClick={handleSave} disabled={!isDirty || !daysValid || isSaving}>
          {isSaving ? "Saving..." : "Save"}
        </Button>
        {showSaved && (
          <span className="text-sm text-secondary-600" role="status" aria-live="polite">
            Saved
          </span>
        )}
      </div>

      {errorMessage && (
        <p className="text-sm text-red-600" role="alert">
          {errorMessage}
        </p>
      )}
    </div>
  )
}
