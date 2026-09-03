// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { GoogleCalendarSettings } from "../GoogleCalendarSettings"
import { IntegrationSettings } from "../IntegrationSettings"
import { SettingsCard } from "../ui"
import { isEnabled } from "@/lib/featureFlags"
import { useConfig } from "@/lib/config"

/** Practice > Calendars. */
export function CalendarsPage() {
  const { googleCalendarEnabled } = useConfig()

  return (
    <>
      {googleCalendarEnabled && (
        <SettingsCard
          title="Google Calendar"
          description="Sessions you book in Pablo appear on your Google Calendar, and busy time from Google blocks booking."
        >
          <GoogleCalendarSettings />
        </SettingsCard>
      )}

      {isEnabled("calendar_integrations") && (
        <SettingsCard
          title="EHR calendars"
          description="Read appointments from another system's calendar feed so Pablo can prepare notes."
        >
          <IntegrationSettings />
        </SettingsCard>
      )}
    </>
  )
}
