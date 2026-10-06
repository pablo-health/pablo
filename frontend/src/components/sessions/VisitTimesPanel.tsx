// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useVisitTimes } from "@/hooks/useVisitTimes"
import { useUserTimeZone } from "@/hooks/usePreferences"
import { clientPresentLineText, clockTime } from "@/lib/visitTimes"
import type { VisitTimes } from "@/types/visitTimes"
import { PsychotherapyWindow } from "./PsychotherapyWindow"

export function visitLineText(times: VisitTimes, timeZone: string): string | null {
  if (!times.started_at || !times.ended_at) return null
  return `Started ${clockTime(times.started_at, timeZone)} · Ended ${clockTime(
    times.ended_at,
    timeZone,
  )} · ${times.total_minutes} min`
}

/**
 * A recorded visit's times, above its note: when it started and ended, how
 * long the client was on the recording, and — for a note with a
 * psychotherapy section — the therapy portion's confirmed minutes.
 *
 * Total time including documentation is shown only for a note without a
 * psychotherapy section: it counts only for a visit chosen by time, and a
 * visit with a psychotherapy add-on never is.
 */
export function VisitTimesPanel({
  sessionId,
  readonly,
}: {
  sessionId: string
  readonly?: boolean
}) {
  const timeZone = useUserTimeZone()
  const { data: times } = useVisitTimes(sessionId)
  if (!times) return null

  const visit = visitLineText(times, timeZone)
  const present = clientPresentLineText(
    { ...times, started_at: times.recording_started_at },
    timeZone,
  )
  const psychotherapy = times.psychotherapy
  if (!visit && !present && !psychotherapy?.offered && times.total_with_documentation_minutes === null) {
    return null
  }

  return (
    <section data-testid="visit-times" aria-label="Visit times" className="mb-4 space-y-2 rounded border border-neutral-200 p-3">
      {visit && (
        <p data-testid="visit-line" className="text-sm text-neutral-700">
          {visit}
        </p>
      )}
      {present && (
        <p data-testid="client-present-line" className="text-sm text-neutral-600">
          {present}
        </p>
      )}
      {psychotherapy?.offered && (
        <PsychotherapyWindow
          sessionId={sessionId}
          window={psychotherapy}
          startedAt={times.recording_started_at}
          timeZone={timeZone}
          readonly={readonly}
        />
      )}
      {times.total_with_documentation_minutes !== null && (
        <p data-testid="documentation-total" className="text-sm text-neutral-600">
          Total time on this date, including documentation: {times.total_with_documentation_minutes} min
        </p>
      )}
    </section>
  )
}
