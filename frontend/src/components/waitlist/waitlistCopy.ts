// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Words for the waitlist, shared by the client page action and the settings
 * list so an entry reads the same in both places.
 */

import {
  DAY_PARTS,
  WEEKDAYS,
  type DayPart,
  type WaitlistEntry,
  type WaitlistModality,
  type Weekday,
} from "@/types/waitlist"

export const DAY_LABELS: Record<Weekday, string> = {
  mon: "Mon",
  tue: "Tue",
  wed: "Wed",
  thu: "Thu",
  fri: "Fri",
  sat: "Sat",
  sun: "Sun",
}

export const DAY_PART_LABELS: Record<DayPart, string> = {
  morning: "Morning",
  afternoon: "Afternoon",
  evening: "Evening",
}

/** "Video" is what a clinician calls telehealth when choosing. */
export const MODALITY_CHOICES: { value: WaitlistModality; label: string }[] = [
  { value: "in_person", label: "In person" },
  { value: "telehealth", label: "Video" },
  { value: "either", label: "Either" },
]

const MODALITY_SUMMARY: Record<WaitlistModality, string> = {
  in_person: "In person",
  telehealth: "Video",
  either: "In person or video",
}

/** The scheduling form's session types; a client's usual one may be something else. */
export const SESSION_TYPE_CHOICES: { value: string; label: string }[] = [
  { value: "individual", label: "Individual" },
  { value: "couples", label: "Couples" },
  { value: "group", label: "Group" },
]

export function sessionTypeLabel(value: string): string {
  const known = SESSION_TYPE_CHOICES.find((choice) => choice.value === value)
  return known ? known.label : value.charAt(0).toUpperCase() + value.slice(1)
}

const WORKWEEK: Weekday[] = ["mon", "tue", "wed", "thu", "fri"]

function daysText(days: Weekday[]): string {
  const ordered = WEEKDAYS.filter((day) => days.includes(day))
  if (ordered.length === WEEKDAYS.length) return "Any day"
  if (ordered.length === WORKWEEK.length && WORKWEEK.every((day) => ordered.includes(day))) {
    return "Weekdays"
  }
  return ordered.map((day) => DAY_LABELS[day]).join(", ")
}

function timesText(times: DayPart[]): string {
  const ordered = DAY_PARTS.filter((part) => times.includes(part))
  if (ordered.length === DAY_PARTS.length) return "Any time of day"
  const words = ordered.map((part) => DAY_PART_LABELS[part].toLowerCase()).join(", ")
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** "Mon, Wed · Morning, evening · Video · Individual" */
export function entrySummary(
  entry: Pick<WaitlistEntry, "days" | "times" | "modality" | "session_type">,
): string {
  return [
    daysText(entry.days),
    timesText(entry.times),
    MODALITY_SUMMARY[entry.modality],
    entry.session_type ? sessionTypeLabel(entry.session_type) : null,
  ]
    .filter(Boolean)
    .join(" · ")
}

export const ADD_ACTION = "Add to waitlist"
export const ON_LIST = "On your waitlist"
export const SAVE = "Add to waitlist"
export const CANCEL = "Cancel"
export const CLOSE = "Close"
export const REMOVE = "Remove"
export const REMOVE_FROM_LIST = "Remove from waitlist"
export const OPEN_LIST = "Open the waitlist"
export const DAYS_LEGEND = "Days"
export const TIMES_LEGEND = "Time of day"
export const MODALITY_LEGEND = "Format"
export const SESSION_TYPE_LABEL = "Session type"
export const ANY_SESSION_TYPE = "No preference"
export const DAYS_REQUIRED = "Choose at least one day."
export const TIMES_REQUIRED = "Choose at least one time of day."
export const MODALITY_REQUIRED = "Choose in person, video or either."
export const ADD_FAILED = "Could not add to the waitlist. Please try again."
export const SAVE_FAILED = "Could not save that change. Please try again."
export const LOAD_FAILED = "Could not load the waitlist."
export const ORDER_CHANGED = "The waitlist changed. Try again with the latest order."
export const PAUSED = "Paused"
export const PAUSE = "Pause"
export const RESUME = "Resume"
export const AUTO_OFFER = "Offer times automatically"

export function notActiveReason(people: { many: string }): string {
  return `Only active ${people.many} can be added to the waitlist.`
}

export function placeLine(position: number): string {
  return `Number ${position} on your list`
}

export function emptyState(people: { one: string }): string {
  return `No one is on your waitlist. Add a ${people.one} from their page.`
}
