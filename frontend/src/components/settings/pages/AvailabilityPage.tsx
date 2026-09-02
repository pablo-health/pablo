// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { AvailabilitySettings } from "../AvailabilitySettings"
import { WorkingHoursSettings } from "@/components/calendar/WorkingHoursSettings"
import { AvailabilityExtras } from "../settingsSlots.extensions"
import { SettingsCard } from "../ui"
import { useSettingsPreferences } from "../useSettingsPreferences"

/**
 * Practice > Availability.
 *
 * Two settings still describe overlapping ideas here: the calendar display
 * window and the working-hours rules that actually drive booking. The display
 * window is on its way out — the calendar will derive its window from the rules
 * — but it stays until that lands so the calendar keeps its scroll behaviour.
 */
export function AvailabilityPage() {
  const { preferences, save, isSaving } = useSettingsPreferences()

  return (
    <>
      {preferences && (
        <SettingsCard
          title="Calendar display hours"
          description="The calendar highlights this window and scrolls to the start of your day."
        >
          <WorkingHoursSettings preferences={preferences} onSave={save} isSaving={isSaving} />
        </SettingsCard>
      )}

      <SettingsCard
        title="Working hours and limits"
        description="Rules that control when appointments can be booked, like blocked days or a maximum per day."
      >
        <AvailabilitySettings />
      </SettingsCard>

      <AvailabilityExtras />
    </>
  )
}
