// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { GoogleCalendarSettings } from "../GoogleCalendarSettings"
import { IntegrationSettings } from "../IntegrationSettings"
import { NameBookingSetting } from "../NameBookingSetting"
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
          description="Sessions you book in Pablo appear on your Google Calendar. With conflict checks on, Pablo won't offer times your calendar shows as busy."
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

      {/* One choice for both: sessions from a followed Google Calendar and
          from a calendar feed book the same way. */}
      {(googleCalendarEnabled || isEnabled("calendar_integrations")) && (
        <SettingsCard title="Sessions from your calendars">
          <NameBookingSetting />
        </SettingsCard>
      )}
    </>
  )
}
