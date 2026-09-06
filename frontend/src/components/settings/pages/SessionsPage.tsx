// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { AudioRetentionSettings } from "../AudioRetentionSettings"
import { SessionDefaults } from "../SessionDefaults"
import { TranscriptionSettings } from "../TranscriptionSettings"
import { SessionsRecordingCard } from "../settingsSlots.extensions"
import { SettingsCard } from "../ui"
import { isEnabled } from "@/lib/featureFlags"
import { useSettingsPreferences, useSettingsUserStatus } from "../useSettingsPreferences"

/**
 * Practice > Sessions & recording.
 *
 * The recording half comes from a slot: this build has no way to grant
 * recording per account, so it shows the controls outright, while a deployment
 * that does gate it renders the no-access and requested states instead.
 */
export function SessionsPage() {
  const { preferences, save, isSaving } = useSettingsPreferences()
  const { data: userStatus } = useSettingsUserStatus()
  const practiceId = userStatus?.practice_id

  return (
    <>
      {preferences && isEnabled("session_defaults") && (
        <SettingsCard title="New appointment defaults" description="Pre-filled on every new appointment.">
          <SessionDefaults preferences={preferences} onSave={save} isSaving={isSaving} />
        </SettingsCard>
      )}

      <SessionsRecordingCard
        fallback={
          <>
            {preferences && isEnabled("transcription") && (
              <SettingsCard title="Transcription" description="How recordings become transcripts.">
                <TranscriptionSettings preferences={preferences} onSave={save} isSaving={isSaving} />
              </SettingsCard>
            )}

            {isEnabled("audio_retention") && practiceId && (
              <SettingsCard
                title="Audio retention"
                description="How long session audio is kept before nightly automatic deletion."
              >
                <AudioRetentionSettings practiceId={practiceId} />
              </SettingsCard>
            )}
          </>
        }
      />
    </>
  )
}
