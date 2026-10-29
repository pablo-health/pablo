// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Psychotherapy minutes and where they fall.
 *
 * The add-on band is shown beside the minutes as a fact; which code applies
 * is decided elsewhere. Minutes are whole minutes, rounded down.
 */

import type { VisitTimes } from "@/types/visitTimes"
import type { PeopleWords } from "./peopleTerm"

export const ADD_ON_MINIMUM_MINUTES = 16
const FIRST_BAND_LAST_MINUTE = 37
const SECOND_BAND_LAST_MINUTE = 52

const SECONDS_PER_MINUTE = 60

/** The add-on band for psychotherapy minutes. */
export function addOnBand(minutes: number): string {
  if (minutes < ADD_ON_MINIMUM_MINUTES) return "Under the add-on minimum"
  if (minutes <= FIRST_BAND_LAST_MINUTE) return "16–37 minutes"
  if (minutes <= SECOND_BAND_LAST_MINUTE) return "38–52 minutes"
  return "53 minutes or more"
}

/** Whole minutes in a span of seconds. */
export function wholeMinutes(seconds: number): number {
  return Math.floor(seconds / SECONDS_PER_MINUTE)
}

/** "11:12 AM" for a point in the recording, or "12:30 in" when the start is unknown. */
export function pointInRecording(
  seconds: number,
  startedAt: string | null,
  timeZone: string,
): string {
  if (!startedAt) {
    const whole = Math.floor(seconds)
    return `${Math.floor(whole / SECONDS_PER_MINUTE)}:${String(whole % SECONDS_PER_MINUTE).padStart(2, "0")} in`
  }
  return clockTime(new Date(new Date(startedAt).getTime() + seconds * 1000), timeZone)
}

export function clockTime(moment: Date | string, timeZone: string): string {
  return new Date(moment).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  })
}

/** "Started 11:00 AM · Ended 11:55 AM · 55 min", or nothing when either end is unknown. */
export function visitLineText(
  times: Pick<VisitTimes, "started_at" | "ended_at" | "total_minutes">,
  timeZone: string,
): string | null {
  if (!times.started_at || !times.ended_at) return null
  return `Started ${clockTime(times.started_at, timeZone)} · Ended ${clockTime(
    times.ended_at,
    timeZone,
  )} · ${times.total_minutes} min`
}

export interface ClientPresentTiming {
  /** When the recording began. */
  started_at: string | null
  client_present_end_seconds: number | null
  clinician_addendum_seconds: number | null
}

/**
 * How long the client was on the recording, and how long the clinician
 * dictated after they left. The server measures the boundary from the
 * recording's two channels (app.notes.client_present). Nothing when it is
 * unknown: an in-person recording, or one made before it was measured.
 */
export function clientPresentLineText(
  timing: ClientPresentTiming,
  timeZone: string,
  people: PeopleWords,
): string | null {
  const boundary = timing.client_present_end_seconds
  if (boundary === null) return null
  const addendum = wholeMinutes(timing.clinician_addendum_seconds ?? 0)
  if (boundary === 0) {
    return addendum > 0 ? `Dictation only, ${addendum} min` : "Dictation only"
  }
  const present = timing.started_at
    ? `${people.One} present until ${pointInRecording(boundary, timing.started_at, timeZone)}`
    : `${people.One} present for ${wholeMinutes(boundary)} min`
  return addendum > 0 ? `${present} · Your dictated addendum: ${addendum} min` : present
}

/**
 * The visit's two durations, as a coded note's header states them: the
 * whole visit, and the therapy alone. Nothing until the therapy minutes are
 * confirmed.
 */
export function durationsLineText(
  times: Pick<VisitTimes, "total_minutes" | "psychotherapy">,
): string | null {
  const therapy = times.psychotherapy
  if (!therapy?.offered || therapy.confirmed_minutes === null) return null
  const total = times.total_minutes !== null ? `Total duration: ${times.total_minutes} min · ` : ""
  return `${total}Psychotherapy duration: ${therapy.confirmed_minutes} min`
}
