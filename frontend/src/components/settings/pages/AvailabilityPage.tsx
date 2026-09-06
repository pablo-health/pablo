// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { BlockedTimeCard, LimitsAndBuffersCard } from "../AvailabilitySettings"
import { WorkingHoursGrid } from "../WorkingHoursGrid"
import { AvailabilityExtras } from "../settingsSlots.extensions"
import { SettingsCard } from "../ui"

/**
 * Practice > Availability.
 *
 * The calendar's window is derived from `working_hours` rules — min start to
 * max end across enabled days — rather than a separate display-hours
 * preference. `WorkingHoursGrid` is the friendly path for that one rule
 * type; `BlockedTimeCard` and `LimitsAndBuffersCard` cover the rest through
 * the same rules engine.
 */
export function AvailabilityPage() {
  return (
    <>
      <SettingsCard
        title="Working hours"
        description="When patients can be booked. Your calendar highlights these hours and opens at your earliest start. There is no separate display setting."
      >
        <WorkingHoursGrid />
      </SettingsCard>

      <AvailabilityExtras />

      <SettingsCard title="Blocked time" description="Recurring breaks, days off and time away." flush>
        <BlockedTimeCard />
      </SettingsCard>

      <SettingsCard title="Limits & buffers" flush>
        <LimitsAndBuffersCard />
      </SettingsCard>
    </>
  )
}
