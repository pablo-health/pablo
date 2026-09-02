// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ComponentType } from "react"
import { AppearancePage } from "./AppearancePage"
import { AvailabilityPage } from "./AvailabilityPage"
import { CalendarsPage } from "./CalendarsPage"
import { PatientPortalPage } from "./PatientPortalPage"
import { ProfilePage } from "./ProfilePage"
import { SchedulingPage } from "./SchedulingPage"
import { SecurityPage } from "./SecurityPage"
import { SessionsPage } from "./SessionsPage"
import { SuperbillsPage } from "./SuperbillsPage"
import { settingsPageExtensions } from "./index.extensions"

/**
 * Which component renders which registry item.
 *
 * A downstream build adds its own pages through `index.extensions.ts` rather
 * than editing this map, so a page added here is never shadowed by a stale
 * downstream copy.
 */
const basePages: Record<string, ComponentType> = {
  profile: ProfilePage,
  appearance: AppearancePage,
  security: SecurityPage,
  availability: AvailabilityPage,
  scheduling: SchedulingPage,
  calendars: CalendarsPage,
  sessions: SessionsPage,
  portal: PatientPortalPage,
  superbills: SuperbillsPage,
}

export const settingsPages: Record<string, ComponentType> = {
  ...basePages,
  ...settingsPageExtensions,
}
