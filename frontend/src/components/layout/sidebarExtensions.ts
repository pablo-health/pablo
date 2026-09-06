// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { Calendar, ClipboardCheck, CreditCard, Home, Settings, Users } from "lucide-react"
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

/**
 * An appended nav item, optionally placed relative to a base item instead of at
 * the end. `insertAfter` names the href of a base nav item; the appended item is
 * spliced in immediately after it. An unset or unmatched `insertAfter` falls back
 * to appending at the end (after the base clinician nav).
 */
export interface AppendNavItem extends NavItem {
  insertAfter?: string
}

/**
 * Shape of the merge slot (`sidebarExtensions.extensions.ts`). Declared here in
 * the stable file so the downstream build's replacement slot can import the type
 * (it can't import it from the file it is replacing).
 *
 * - `overrides`: per-href patches applied to base items (relabel / re-icon).
 * - `append`: items added to the clinician nav — at the end by default, or after
 *   a named base item via `insertAfter`.
 */
export interface NavExtensions {
  overrides: Record<string, Partial<Omit<NavItem, "href">>>
  append: AppendNavItem[]
}

const baseClinicianNavigation: NavItem[] = [
  { name: "Dashboard", href: "/dashboard", icon: Home },
  { name: "Calendar", href: "/dashboard/calendar", icon: Calendar },
  { name: "Patients", href: "/dashboard/patients", icon: Users },
  { name: "Review", href: "/dashboard/sessions", icon: ClipboardCheck },
  { name: "Billing", href: "/dashboard/billing", icon: CreditCard },
]

/** Apply the slot's per-href patches (relabel / re-icon) to the base items. */
function applyOverrides(items: NavItem[]): NavItem[] {
  return items.map((item) =>
    navExtensions.overrides[item.href] ? { ...item, ...navExtensions.overrides[item.href] } : item
  )
}

/**
 * Merge the slot's appended items into the base nav. An item with `insertAfter`
 * is spliced immediately after the named base href; everything else is appended
 * at the end, preserving slot order.
 */
function mergeAppended(base: NavItem[]): NavItem[] {
  const items = [...base]
  for (const { insertAfter, ...item } of navExtensions.append) {
    const idx = insertAfter ? items.findIndex((existing) => existing.href === insertAfter) : -1
    if (idx === -1) {
      items.push(item)
    } else {
      items.splice(idx + 1, 0, item)
    }
  }
  return items
}

export const clinicianNavigation: NavItem[] = mergeAppended(applyOverrides(baseClinicianNavigation))

export const settingsItem: NavItem = {
  name: "Settings",
  href: "/dashboard/settings",
  icon: Settings,
}
