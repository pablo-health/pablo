// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { RecordActivityItem } from "@/lib/api/patientActivity"

/**
 * A new client's first two minutes, as Record activity received it: the
 * clinician creates the client and writes and signs one note. 31 rows, about
 * five of them meaningful.
 *
 * Reconstructed from a reported trail, not captured: the counts per action are
 * the reported ones (the chart viewed 14 times, five in one second; a session
 * opened 8 times, in pairs within a second; the waitlist and the AI consent
 * line each read twice), and the times between visits are spaced the way the
 * app writes them, one burst of rows per page load.
 */
const NEWEST_FIRST: Array<[string, string]> = [
  ["16:38:30", "patient_activity_viewed"],
  ["16:38:16", "patient_viewed"],
  ["16:38:16", "patient_viewed"],
  ["16:38:15", "patient_viewed"],
  ["16:38:15", "patient_viewed"],
  ["16:38:06", "patient_viewed"],
  ["16:38:06", "patient_viewed"],
  ["16:38:05", "patient_viewed"],
  ["16:38:05", "patient_viewed"],
  ["16:38:05", "patient_viewed"],
  ["16:38:02", "session_viewed"],
  ["16:38:02", "session_viewed"],
  ["16:37:56", "session_viewed"],
  ["16:37:56", "session_viewed"],
  ["16:37:55", "note_signed"],
  ["16:37:41", "session_viewed"],
  ["16:37:41", "session_viewed"],
  ["16:37:40", "session_updated"],
  ["16:37:21", "session_viewed"],
  ["16:37:21", "session_viewed"],
  ["16:37:20", "session_created"],
  ["16:37:10", "patient_viewed"],
  ["16:37:10", "patient_ai_consent_viewed"],
  ["16:37:10", "patient_viewed"],
  ["16:37:10", "waitlist_viewed"],
  ["16:37:10", "patient_viewed"],
  ["16:37:10", "patient_ai_consent_viewed"],
  ["16:37:10", "patient_viewed"],
  ["16:37:10", "waitlist_viewed"],
  ["16:37:10", "patient_viewed"],
  ["16:37:08", "patient_created"],
]

const RESOURCE: Record<string, string> = {
  session_viewed: "session",
  session_updated: "session",
  session_created: "session",
  note_signed: "session",
}

export const NEW_CLIENT_FIRST_VISIT: RecordActivityItem[] = NEWEST_FIRST.map(
  ([time, action], index) => ({
    id: `row-${String(index).padStart(2, "0")}`,
    timestamp: `2026-11-15T${time}.${String(900 - index * 10).padStart(3, "0")}Z`,
    actor_type: "clinician",
    actor_name: "Sam Owner",
    is_you: true,
    action,
    resource_type: RESOURCE[action] ?? "patient",
  })
)
