// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import {
  Ban,
  Check,
  CheckCheck,
  CircleSlash,
  type LucideIcon,
} from "lucide-react"

/**
 * Appointment status presentation for the editorial calendar.
 *
 * Each status pairs its color tokens with a non-color cue (a distinct icon
 * shape + a label) so confirmed / completed / cancelled / no-show stay
 * distinguishable without relying on color — color-blind-safe, and the label
 * feeds aria-labels for screen readers.
 */
export interface EditorialStatusMeta {
  label: string
  Icon: LucideIcon
  bg: string
  fg: string
  rail: string
}

const META: Record<string, EditorialStatusMeta> = {
  confirmed: {
    label: "Confirmed",
    Icon: Check,
    bg: "var(--ed-status-confirmed-bg)",
    fg: "var(--ed-status-confirmed-fg)",
    rail: "var(--ed-status-confirmed-rail)",
  },
  completed: {
    label: "Completed",
    Icon: CheckCheck,
    bg: "var(--ed-status-completed-bg)",
    fg: "var(--ed-status-completed-fg)",
    rail: "var(--ed-status-completed-rail)",
  },
  cancelled: {
    label: "Cancelled",
    Icon: Ban,
    bg: "var(--ed-status-cancelled-bg)",
    fg: "var(--ed-status-cancelled-fg)",
    rail: "var(--ed-status-cancelled-rail)",
  },
  no_show: {
    label: "No-show",
    Icon: CircleSlash,
    bg: "var(--ed-status-noshow-bg)",
    fg: "var(--ed-status-noshow-fg)",
    rail: "var(--ed-status-noshow-rail)",
  },
}

export function editorialStatusMeta(status: string): EditorialStatusMeta {
  return META[status] ?? META.confirmed
}
