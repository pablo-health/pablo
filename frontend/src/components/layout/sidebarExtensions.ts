// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { Calendar, ClipboardCheck, Home, Settings, Users } from "lucide-react"
import type { LucideIcon } from "lucide-react"

/**
 * Sidebar navigation config — the extension point for `Sidebar.tsx`.
 *
 * A downstream build (e.g. a deployment overlay) may overwrite *this file only*
 * to relabel/re-icon the clinician nav items; `Sidebar.tsx` itself is never
 * forked. Keeping the component out of the overlay means a change to the
 * sidebar's markup or behaviour can't go stale in a downstream copy.
 */
export interface NavItem {
  name: string
  href: string
  icon: LucideIcon
}

export const clinicianNavigation: NavItem[] = [
  { name: "Dashboard", href: "/dashboard", icon: Home },
  { name: "Calendar", href: "/dashboard/calendar", icon: Calendar },
  { name: "Patients", href: "/dashboard/patients", icon: Users },
  { name: "Review", href: "/dashboard/sessions", icon: ClipboardCheck },
]

export const settingsItem: NavItem = {
  name: "Settings",
  href: "/dashboard/settings",
  icon: Settings,
}
