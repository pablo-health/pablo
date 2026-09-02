// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { BookingLinkSettings } from "../BookingLinkSettings"
import { SchedulingEmailReplies } from "../settingsSlots.extensions"
import { SettingsCard } from "../ui"
import { useConfig } from "@/lib/config"

/**
 * Practice > Scheduling.
 *
 * Appointment types as first-class rows and the new-patient flow arrive with
 * the per-type scheduling columns. Until then this page is the public booking
 * pages, re-housed from the old settings list.
 */
export function SchedulingPage() {
  const { publicBookingEnabled } = useConfig()

  if (!publicBookingEnabled) {
    return (
      <SettingsCard title="Public booking pages">
        <p className="text-sm text-muted-foreground">
          Public booking is turned off for this deployment.
        </p>
      </SettingsCard>
    )
  }

  return (
    <>
      <SettingsCard
        title="Public booking pages"
        description="Pages where patients pick a time. Each page books at a fixed length."
      >
        <BookingLinkSettings />
      </SettingsCard>

      <SchedulingEmailReplies />
    </>
  )
}
