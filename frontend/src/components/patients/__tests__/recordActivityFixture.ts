// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { RecordActivityItem } from "@/lib/api/patientActivity"

/**
 * A new client's first two minutes, as Record activity received it: the
 * clinician creates the client, opens the chart, and writes and signs one
 * note. 31 rows, about five of them meaningful.
 *
 * Timed from a captured prod request log; actions mapped from each route's
 * audit write. Offsets are seconds after the client was created, on a
 * synthetic date, with no ids from the log. Each request becomes the row its
 * route writes:
 *
 * - `POST /patients`: patient_created
 * - a chart load (`GET /patients/{id}` and the chart reads beside it):
 *   patient_viewed, plus `/waitlist` (waitlist_viewed) and `/ai-consent`
 *   (patient_ai_consent_viewed) in the same burst
 * - `GET /patients/{id}/activity`: patient_activity_viewed, written after the
 *   read, so the second visit's row is not in its own page
 * - `POST /patients/{id}/notes`: session_created
 * - `GET /notes/{id}` and `GET /notes/{id}/signing`: session_viewed
 * - `PATCH /notes/{id}`: session_updated; `POST /notes/{id}/sign`: note_signed
 *
 * The code agrees with the reported counts per action, so those are kept
 * (the chart viewed 14 times, a session opened 8 times, the waitlist and the
 * AI consent line each read twice). The log gives each burst's time but not
 * how the reported counts split across bursts: the 14 chart views are placed
 * as 7, 1 and 6, and three of the 8 session opens fall in the burst of note
 * reads after the edit. The line count does not depend on that split, only on
 * which actions land when.
 */
const OLDEST_FIRST: Array<[number, string]> = [
  [0.0, "patient_created"],
  // Chart load 1, 16:37:09.07-09.38 in the log.
  [6.47, "patient_viewed"],
  [6.5, "waitlist_viewed"],
  [6.52, "patient_ai_consent_viewed"],
  [6.55, "patient_viewed"],
  [6.6, "patient_viewed"],
  [6.64, "patient_viewed"],
  [6.68, "patient_viewed"],
  [6.73, "patient_viewed"],
  [6.78, "patient_viewed"],
  [12.4, "patient_activity_viewed"],
  [49.5, "session_created"],
  [50.0, "patient_viewed"],
  [50.3, "session_viewed"],
  [50.7, "session_viewed"],
  [59.7, "session_updated"],
  [60.0, "session_viewed"],
  [62.9, "session_viewed"],
  [62.95, "session_viewed"],
  [63.0, "session_viewed"],
  [66.6, "note_signed"],
  [67.0, "session_viewed"],
  [67.05, "session_viewed"],
  // Chart load 2, 16:38:14.3-15.94 in the log.
  [71.7, "patient_viewed"],
  [71.75, "waitlist_viewed"],
  [71.8, "patient_ai_consent_viewed"],
  [72.1, "patient_viewed"],
  [72.5, "patient_viewed"],
  [72.9, "patient_viewed"],
  [73.1, "patient_viewed"],
  [73.34, "patient_viewed"],
]

const RESOURCE: Record<string, string> = {
  session_viewed: "session",
  session_updated: "session",
  session_created: "session",
  note_signed: "session",
}

const CREATED_AT = Date.parse("2026-01-05T15:00:00.000Z")

export const NEW_CLIENT_FIRST_VISIT: RecordActivityItem[] = [...OLDEST_FIRST]
  .reverse()
  .map(([offset, action], index) => ({
    id: `row-${String(index).padStart(2, "0")}`,
    timestamp: new Date(CREATED_AT + Math.round(offset * 1000)).toISOString(),
    actor_type: "clinician",
    actor_name: "Sam Owner",
    is_you: true,
    action,
    resource_type: RESOURCE[action] ?? "patient",
  }))
