// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import {
  Calendar,
  CalendarClock,
  Clock,
  Mic,
  Palette,
  Receipt,
  ShieldCheck,
  ShieldPlus,
  User,
  Users,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"
import type { ComponentType } from "react"
import { AppearancePage } from "./pages/AppearancePage"
import { AvailabilityPage } from "./pages/AvailabilityPage"
import { CalendarsPage } from "./pages/CalendarsPage"
import { InsurancePage } from "./pages/InsurancePage"
import { PatientPortalPage } from "./pages/PatientPortalPage"
import { ProfilePage } from "./pages/ProfilePage"
import { SchedulingPage } from "./pages/SchedulingPage"
import { SecurityPage } from "./pages/SecurityPage"
import { SessionsPage } from "./pages/SessionsPage"
import { SuperbillsPage } from "./pages/SuperbillsPage"
import { settingsExtensions } from "./registry.extensions"

/**
 * The settings information architecture — the extension point for the settings
 * shell.
 *
 * The base groups live here; a downstream build customises them through the
 * `registry.extensions.ts` *merge slot* (relabel an item by id, or append items
 * and whole groups) rather than overwriting this file or forking the settings
 * page. That way an item added here is never silently shadowed by a stale
 * downstream copy — the failure this replaced, where a forked settings page
 * quietly dropped four sections and nobody noticed for months.
 *
 * Content that differs *inside* a shared page goes through the separate render
 * slot in `settingsSlots.extensions.tsx`, not through a second page.
 */
export interface SettingsItem {
  /** URL segment and stable identity across builds. */
  id: string
  label: string
  icon: LucideIcon
  /** One line, shown under the page title and searched by the nav filter. */
  desc: string
  /** What renders at `/dashboard/settings/<id>`. */
  page: ComponentType
  /**
   * Names an optional feature (see `useFeature`). While it is off the item is
   * absent from the nav *and* its route 404s — a hidden nav link is not a gate.
   */
  feature?: string
}

export interface SettingsGroup {
  id: string
  label: string
  items: SettingsItem[]
}

/**
 * An appended item, optionally placed relative to an existing item instead of
 * at the end of its group. `insertAfter` / `insertBefore` name an item id in
 * the target group; an unset or unmatched hint falls back to appending.
 */
export interface AppendSettingsItem extends SettingsItem {
  /** Id of the group to append into. An unknown group is ignored. */
  group: string
  insertAfter?: string
  insertBefore?: string
}

/** An appended group, optionally placed after a named base group. */
export interface AppendSettingsGroup extends SettingsGroup {
  insertAfter?: string
}

/**
 * Shape of the merge slot. Declared here in the stable file so the downstream
 * build's replacement slot can import the type (it cannot import it from the
 * file it is replacing).
 */
export interface SettingsExtensions {
  /** Per-item-id patches applied to base items (relabel / re-icon / re-gate). */
  overrides: Record<string, Partial<Omit<SettingsItem, "id">>>
  appendItems: AppendSettingsItem[]
  appendGroups: AppendSettingsGroup[]
}

const baseGroups: SettingsGroup[] = [
  {
    id: "you",
    label: "You",
    items: [
      { id: "profile", label: "Profile", icon: User, page: ProfilePage, desc: "Your name, timezone and clinician type." },
      { id: "appearance", label: "Appearance", icon: Palette, page: AppearancePage, desc: "How your workspace looks." },
      { id: "security", label: "Sign-in & security", icon: ShieldCheck, page: SecurityPage, desc: "Passkeys and second factors." },
    ],
  },
  {
    id: "practice",
    label: "Practice",
    items: [
      {
        id: "availability",
        label: "Availability",
        icon: Clock,
        page: AvailabilityPage,
        desc: "When you see patients. Drives booking, reminders and your calendar view.",
      },
      {
        id: "scheduling",
        label: "Scheduling",
        icon: CalendarClock,
        page: SchedulingPage,
        desc: "Which appointments exist, how new patients start, and what Pablo may offer versus what patients may book.",
      },
      {
        id: "calendars",
        label: "Calendars",
        icon: Calendar,
        page: CalendarsPage,
        desc: "Google Calendar and EHR calendars synced into Pablo.",
      },
      {
        id: "sessions",
        label: "Sessions & recording",
        icon: Mic,
        page: SessionsPage,
        desc: "Defaults for new appointments and how recordings are handled.",
      },
      {
        id: "portal",
        label: "Patient portal",
        icon: Users,
        page: PatientPortalPage,
        feature: "patient_portal",
        desc: "Intake forms, self-report measures and patient sign-in.",
      },
    ],
  },
  {
    id: "billing",
    label: "Billing",
    items: [
      {
        id: "superbills",
        label: "Superbills & rates",
        icon: Receipt,
        page: SuperbillsPage,
        feature: "superbills",
        desc: "Out-of-network receipts generated from the chart.",
      },
      {
        id: "insurance",
        label: "Insurance payers",
        icon: ShieldPlus,
        page: InsurancePage,
        desc: "Who you file claims with, and the filing deadlines each payer holds you to.",
      },
    ],
  },
]

/** Apply the slot's per-id patches to the base items. */
function applyOverrides(groups: SettingsGroup[]): SettingsGroup[] {
  const { overrides } = settingsExtensions
  return groups.map((group) => ({
    ...group,
    items: group.items.map((item) => (overrides[item.id] ? { ...item, ...overrides[item.id] } : item)),
  }))
}

/** Splice one appended item into its group at the requested position. */
function insertItem(items: SettingsItem[], entry: AppendSettingsItem): SettingsItem[] {
  const { group: _group, insertAfter, insertBefore, ...item } = entry
  const next = [...items]
  const afterIdx = insertAfter ? next.findIndex((existing) => existing.id === insertAfter) : -1
  if (afterIdx !== -1) {
    next.splice(afterIdx + 1, 0, item)
    return next
  }
  const beforeIdx = insertBefore ? next.findIndex((existing) => existing.id === insertBefore) : -1
  if (beforeIdx !== -1) {
    next.splice(beforeIdx, 0, item)
    return next
  }
  next.push(item)
  return next
}

/** Merge the slot's appended groups and items into the base IA. */
function mergeAppended(groups: SettingsGroup[]): SettingsGroup[] {
  const merged = [...groups]

  for (const { insertAfter, ...group } of settingsExtensions.appendGroups) {
    const idx = insertAfter ? merged.findIndex((existing) => existing.id === insertAfter) : -1
    if (idx === -1) {
      merged.push(group)
    } else {
      merged.splice(idx + 1, 0, group)
    }
  }

  return merged.map((group) => {
    const additions = settingsExtensions.appendItems.filter((entry) => entry.group === group.id)
    if (additions.length === 0) return group
    return { ...group, items: additions.reduce(insertItem, group.items) }
  })
}

/** Every group and item this build knows about, before gating. */
export const settingsGroups: SettingsGroup[] = mergeAppended(applyOverrides(baseGroups))

/** Flat item list with each item's group label attached, for lookup and search. */
export interface ResolvedSettingsItem extends SettingsItem {
  groupId: string
  groupLabel: string
}

export const settingsItems: ResolvedSettingsItem[] = settingsGroups.flatMap((group) =>
  group.items.map((item) => ({ ...item, groupId: group.id, groupLabel: group.label }))
)

/** The item a URL segment addresses, regardless of whether its gate allows it. */
export function findSettingsItem(id: string): ResolvedSettingsItem | undefined {
  return settingsItems.find((item) => item.id === id)
}

/** Where `/dashboard/settings` sends you. */
export const DEFAULT_SETTINGS_ITEM = "profile"
