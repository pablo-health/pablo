// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { differenceInCalendarDays, format } from "date-fns"
import {
  Ban,
  Check,
  CheckCheck,
  CircleSlash,
  Hourglass,
  SkipForward,
  type LucideIcon,
} from "lucide-react"
import type { AppointmentResponse, AppointmentStatus } from "@/types/scheduling"

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
  // A held time shares the confirmed colours; the card's dashed, striped
  // treatment is what tells it apart, so the two never compete for a hue.
  pending: {
    label: "Held",
    Icon: Hourglass,
    bg: "var(--ed-status-confirmed-bg)",
    fg: "var(--ed-status-confirmed-fg)",
    rail: "var(--ed-status-confirmed-rail)",
  },
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
  skipped: {
    label: "Skipped",
    Icon: SkipForward,
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

/**
 * What the calendar shows an appointment as: its status, except that a
 * cancelled occurrence the series carries on past shows as `skipped`. It is
 * also the key the "Show on calendar" filters use, so skipped weeks can stay
 * visible while ordinary cancellations are hidden.
 */
export type CalendarShowing = AppointmentStatus | "skipped"

export function calendarShowing(
  appointment: Pick<AppointmentResponse, "status" | "skipped">,
): CalendarShowing {
  return appointment.status === "cancelled" && appointment.skipped
    ? "skipped"
    : appointment.status
}

/**
 * "Held until Fri" for a held time: the weekday within the coming week, the
 * time when it lapses today, the date beyond that. Plain "Held" when the
 * expiry is unknown.
 */
export function heldLabel(expiresAt: string | null | undefined, now: Date = new Date()): string {
  if (!expiresAt) return "Held"
  const until = new Date(expiresAt)
  const days = differenceInCalendarDays(until, now)
  if (days <= 0) return `Held until ${format(until, "h:mm a")}`
  if (days < 7) return `Held until ${format(until, "EEE")}`
  return `Held until ${format(until, "MMM d")}`
}
