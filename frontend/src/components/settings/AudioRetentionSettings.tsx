// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { useAudioRetention } from "@/hooks/useAudioRetention"
import { ApiError } from "@/lib/api/client"
import {
  AUDIO_RETENTION_DEFAULT_DAYS,
  AUDIO_RETENTION_MAX_DAYS,
  AUDIO_RETENTION_MIN_DAYS,
} from "@/lib/api/practices"

interface AudioRetentionSettingsProps {
  practiceId: string
  /**
   * Current retention window from the backend, if known. If omitted,
   * the slider initializes at the documented default (365 days). The
   * SaaS endpoint accepts only PUT — there is no GET — so callers
   * that don't already have the value must accept this default.
   */
  initialDays?: number
}

export function AudioRetentionSettings({
  practiceId,
  initialDays,
}: AudioRetentionSettingsProps) {
  const persisted = initialDays ?? AUDIO_RETENTION_DEFAULT_DAYS
  const [days, setDays] = useState<number>(persisted)
  const [savedDays, setSavedDays] = useState<number>(persisted)
  const [showSaved, setShowSaved] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const mutation = useAudioRetention()

  const isDirty = days !== savedDays
  const isSaving = mutation.isPending

  const handleSave = () => {
    setErrorMessage(null)
    mutation.mutate(
      { practiceId, days },
      {
        onSuccess: (data) => {
          setSavedDays(data.audio_retention_days)
          setDays(data.audio_retention_days)
          setShowSaved(true)
          window.setTimeout(() => setShowSaved(false), 2000)
        },
        onError: (err) => {
          if (err instanceof ApiError && err.status === 400) {
            setErrorMessage(
              `Days must be between ${AUDIO_RETENTION_MIN_DAYS} and ${AUDIO_RETENTION_MAX_DAYS}.`,
            )
          } else if (err instanceof ApiError && err.status === 422) {
            setErrorMessage(
              `Days must be between ${AUDIO_RETENTION_MIN_DAYS} and ${AUDIO_RETENTION_MAX_DAYS}.`,
            )
          } else {
            setErrorMessage(
              err instanceof Error
                ? err.message
                : "Failed to update audio retention.",
            )
          }
        },
      },
    )
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-2 max-w-md">
        <div className="flex items-center justify-between">
          <Label htmlFor="audio-retention-days">Retention window</Label>
          <span
            className="text-sm font-medium text-neutral-900"
            aria-live="polite"
            data-testid="audio-retention-value"
          >
            {days} days
          </span>
        </div>
        <input
          id="audio-retention-days"
          type="range"
          min={AUDIO_RETENTION_MIN_DAYS}
          max={AUDIO_RETENTION_MAX_DAYS}
          step={1}
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          disabled={isSaving}
          aria-valuemin={AUDIO_RETENTION_MIN_DAYS}
          aria-valuemax={AUDIO_RETENTION_MAX_DAYS}
          aria-valuenow={days}
          className="w-full h-2 bg-neutral-200 rounded-lg appearance-none cursor-pointer accent-primary disabled:opacity-50 disabled:cursor-not-allowed"
        />
        <div className="flex justify-between text-xs text-neutral-500">
          <span>{AUDIO_RETENTION_MIN_DAYS} days</span>
          <span>{AUDIO_RETENTION_MAX_DAYS} days</span>
        </div>
      </div>

      <p className="text-sm text-neutral-600">
        Recordings older than {days} days are deleted nightly; each deletion
        writes an audit log row.
      </p>

      <div className="flex items-center gap-3">
        <Button
          size="sm"
          onClick={handleSave}
          disabled={!isDirty || isSaving}
        >
          {isSaving ? "Saving..." : "Save"}
        </Button>
        {showSaved && (
          <span
            className="text-sm text-secondary-600"
            role="status"
            aria-live="polite"
          >
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
