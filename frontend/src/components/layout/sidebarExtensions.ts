// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { Calendar, ClipboardCheck, Home, Settings, Users } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { navExtensions } from "./sidebarExtensions.extensions"

/**
 * Sidebar navigation config — the extension point for `Sidebar.tsx`.
 *
 * The base nav lives here; a downstream build customises it through the
 * `sidebarExtensions.extensions.ts` *merge slot* (relabel/re-icon an item by
 * href, or append new items) rather than overwriting this whole file. That way
 * a base nav item added here is never silently shadowed by a stale downstream
 * copy — the same merge-slot discipline as `queryKeys.extensions.ts`.
 * `Sidebar.tsx` itself is never forked.
 */
export interface NavItem {
  name: string
  href: string
  icon: LucideIcon
  /**
   * Optional visibility gates evaluated at render by `useNavVisibility`
   * (`sidebarVisibility.ts`). The base build ignores them (everything shows);
   * a downstream overlay can gate an item on a plan capability and/or a runtime
   * feature flag without touching this file or the component.
   */
  requiresCapability?: string
  requiresFlag?: string
}

const baseClinicianNavigation: NavItem[] = [
  { name: "Dashboard", href: "/dashboard", icon: Home },
  { name: "Calendar", href: "/dashboard/calendar", icon: Calendar },
  { name: "Patients", href: "/dashboard/patients", icon: Users },
  { name: "Review", href: "/dashboard/sessions", icon: ClipboardCheck },
]

/** Apply the slot's per-href patches (relabel / re-icon) to the base items. */
function applyOverrides(items: NavItem[]): NavItem[] {
  return items.map((item) =>
    navExtensions.overrides[item.href] ? { ...item, ...navExtensions.overrides[item.href] } : item
  )
}

export const clinicianNavigation: NavItem[] = [
  ...applyOverrides(baseClinicianNavigation),
  ...navExtensions.append,
]

export const settingsItem: NavItem = {
  name: "Settings",
  href: "/dashboard/settings",
  icon: Settings,
}
