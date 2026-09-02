// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { ProfileSettings } from "../ProfileSettings"
import { ProviderTypeSettings } from "../ProviderTypeSettings"
import { SettingsCard } from "../ui"
import { useSettingsPreferences, useSettingsUserStatus } from "../useSettingsPreferences"

/**
 * You > Profile.
 *
 * Today this edits a display name and the clinician type. The full profile —
 * licence, NPI, practice name, address, phone and timezone — arrives with the
 * backend fields that back them.
 */
export function ProfilePage() {
  const { preferences, save, isSaving } = useSettingsPreferences()
  const { data: userStatus } = useSettingsUserStatus()

  if (!preferences) return null

  return (
    <>
      <SettingsCard title="You" description="Shown on notes, reports and anything Pablo sends on your behalf.">
        <ProfileSettings preferences={preferences} onSave={save} isSaving={isSaving} />
      </SettingsCard>

      <SettingsCard
        title="Clinician type"
        description="Sets the note template and prompts Pablo uses for your visits."
      >
        <ProviderTypeSettings currentValue={userStatus?.provider_type ?? null} />
      </SettingsCard>
    </>
  )
}
