// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { AiNotesConsentCard } from "../AiNotesConsentCard"
import { AudioRetentionSettings } from "../AudioRetentionSettings"
import { SessionDefaults } from "../SessionDefaults"
import { TelehealthSettings } from "../TelehealthSettings"
import { TranscriptionSettings } from "../TranscriptionSettings"
import { SessionsRecordingCard } from "../settingsSlots.extensions"
import { SettingsCard } from "../ui"
import { isEnabled } from "@/lib/featureFlags"
import { useSettingsPreferences } from "../useSettingsPreferences"

/**
 * Practice > Sessions & recording.
 *
 * The recording half comes from a slot: this build has no way to grant
 * recording per account, so it shows the controls outright, while a deployment
 * that does gate it renders the no-access and requested states instead.
 */
export function SessionsPage() {
  const { preferences, save, isSaving } = useSettingsPreferences()

  return (
    <>
      {preferences && isEnabled("session_defaults") && (
        <SettingsCard title="New appointment defaults" description="Pre-filled on every new appointment.">
          <SessionDefaults preferences={preferences} onSave={save} isSaving={isSaving} />
        </SettingsCard>
      )}

      <SettingsCard
        title="Video sessions"
        description="Where a session happens when you meet online. Pablo uses the service you already have."
      >
        <TelehealthSettings />
      </SettingsCard>

      <SessionsRecordingCard
        fallback={
          <>
            {preferences && isEnabled("transcription") && (
              <SettingsCard title="Transcription" description="How recordings become transcripts.">
                <TranscriptionSettings preferences={preferences} onSave={save} isSaving={isSaving} />
              </SettingsCard>
            )}

            {isEnabled("audio_retention") && (
              <SettingsCard title="Audio retention" description="When session audio is deleted.">
                <AudioRetentionSettings />
              </SettingsCard>
            )}
          </>
        }
      />

      {/* Outside the recording slot and the retention flag: whether to ask
          clients is a practice decision in every build. */}
      <AiNotesConsentCard />
    </>
  )
}
