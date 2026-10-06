// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { useUserTimeZone } from "@/hooks/usePreferences"
import type { PeopleWords } from "@/lib/peopleTerm"

export interface ClientPresentTiming {
  started_at: string | null
  client_present_end_seconds?: number | null
  clinician_addendum_seconds?: number | null
}

const SECONDS_PER_MINUTE = 60

function wholeMinutes(seconds: number): number {
  return Math.floor(seconds / SECONDS_PER_MINUTE)
}

/**
 * The line under a recorded session's note heading: how long the client was
 * on the recording, and how long the clinician dictated after they left.
 *
 * The boundary comes from the recording's two channels (the server measures
 * it; see app.notes.client_present). Minutes are whole minutes, rounded down,
 * because they feed time-based codes. Nothing when the boundary is unknown —
 * an in-person recording, or one made before it was measured.
 */
export function clientPresentLineText(
  timing: ClientPresentTiming,
  timeZone: string,
  people: PeopleWords,
): string | null {
  const boundary = timing.client_present_end_seconds
  if (boundary === null || boundary === undefined) return null
  const addendum = wholeMinutes(timing.clinician_addendum_seconds ?? 0)
  if (boundary === 0) {
    return addendum > 0 ? `Dictation only, ${addendum} min` : "Dictation only"
  }
  const present = timing.started_at
    ? `${people.One} present until ${new Date(
        new Date(timing.started_at).getTime() + boundary * 1000,
      ).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone })}`
    : `${people.One} present for ${wholeMinutes(boundary)} min`
  return addendum > 0 ? `${present} · Your dictated addendum: ${addendum} min` : present
}

export function ClientPresentLine({ timing }: { timing: ClientPresentTiming }) {
  const timeZone = useUserTimeZone()
  const people = usePeopleTerm()
  const text = clientPresentLineText(timing, timeZone, people)
  if (!text) return null
  return (
    <p data-testid="client-present-line" className="mb-3 text-sm text-neutral-600">
      {text}
    </p>
  )
}
