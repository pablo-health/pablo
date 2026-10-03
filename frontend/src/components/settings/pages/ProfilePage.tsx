// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { PeopleTermSettings } from "../PeopleTermSettings"
import { PracticeNameSettings } from "../PracticeNameSettings"
import { ProfileSettings } from "../ProfileSettings"
import { ProviderTypeSettings } from "../ProviderTypeSettings"
import { SettingsCard } from "../ui"
import { useSettingsPreferences, useSettingsUserStatus } from "../useSettingsPreferences"

/**
 * You > Profile.
 *
 * Edits a display name, the clinician type, whether the app says clients or
 * patients, and — for the practice owner — the practice name and the practice's
 * default word. The rest of the profile (licence, NPI, address, phone,
 * timezone) lives on the billing pages or arrives with the fields behind it.
 */
export function ProfilePage() {
  const { preferences, save, isSaving } = useSettingsPreferences()
  const { data: userStatus } = useSettingsUserStatus()
  const people = usePeopleTerm()

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

      <SettingsCard
        // people-term-ok: this card is the choice between the two words
        title="Clients or patients"
        description="The word Pablo uses for the people you see."
      >
        <PeopleTermSettings />
      </SettingsCard>

      {userStatus?.practice_name !== undefined && (
        <SettingsCard
          title="Practice"
          description={`The name your ${people.many} see in the portal and on forms.`}
        >
          <PracticeNameSettings
            currentName={userStatus.practice_name}
            canEdit={userStatus.is_practice_owner === true}
          />
        </SettingsCard>
      )}
    </>
  )
}
