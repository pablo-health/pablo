// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { AppointmentTypesCard } from "../AppointmentTypesCard"
import { BookingLinkSettings } from "../BookingLinkSettings"
import { SchedulingEmailReplies, SchedulingExtras } from "../settingsSlots.extensions"
import { SettingsCard } from "../ui"
import { useConfig } from "@/lib/config"

/**
 * Practice > Scheduling.
 *
 * The new-patient flow and the self-booking split are still the public
 * booking pages, re-housed from the old settings list, until their own cards
 * land (THERAPY-9zgyw.19, .20).
 */
export function SchedulingPage() {
  const { publicBookingEnabled } = useConfig()

  return (
    <>
      <AppointmentTypesCard />

      <SchedulingExtras />

      {publicBookingEnabled && (
        <SettingsCard
          title="Public booking pages"
          description="Pages where patients pick a time. Each page books at a fixed length."
        >
          <BookingLinkSettings />
        </SettingsCard>
      )}

      <SchedulingEmailReplies />
    </>
  )
}
