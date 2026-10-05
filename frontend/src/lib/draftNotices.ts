// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Draft notices — telling the clinician, on whatever screen they are on, that
 * a session's draft is ready or could not be written.
 *
 * The rules live here so the hook stays wiring: which statuses mean a draft is
 * still being written, which mean it has landed, how a session is named in the
 * notice, and which sessions have already been announced.
 *
 * A notice names a session by its weekday and time only. That is enough to
 * tell two of today's sessions apart, and it keeps the client's name off a
 * banner that can appear over any screen.
 */

import type { SessionResponse, SessionStatus } from "@/types/sessions"

export type DraftOutcome = "ready" | "failed"

export interface DraftNotice {
  sessionId: string
  outcome: DraftOutcome
  /** "Tuesday 2:00 PM" — the session's weekday and time. */
  sessionLabel: string
}

/** A session in one of these is still on its way to a draft. */
const DRAFTING: ReadonlySet<SessionStatus> = new Set([
  "recording_complete",
  "queued",
  "processing",
])

/**
 * A session that has sat in a drafting status this long is stuck rather than
 * drafting. It is not watched, so a page left open does not poll it forever;
 * the clinician still sees its status wherever sessions are listed.
 */
export const DRAFT_WATCH_WINDOW_MS = 2 * 60 * 60 * 1000

export function isDrafting(status: SessionStatus): boolean {
  return DRAFTING.has(status)
}

export function draftOutcome(status: SessionStatus): DraftOutcome | null {
  if (status === "pending_review") return "ready"
  if (status === "failed") return "failed"
  return null
}

/** Whether a session in a list is one whose draft is worth watching for. */
export function isWatchableDraft(
  session: Pick<SessionResponse, "status" | "created_at" | "updated_at">,
  now: number = Date.now(),
): boolean {
  if (!isDrafting(session.status)) return false
  const touched = Date.parse(session.updated_at ?? session.created_at)
  return Number.isNaN(touched) || now - touched < DRAFT_WATCH_WINDOW_MS
}

export function draftSessionLabel(sessionDate: string): string {
  // ICU versions differ: some join with " at ", newer ones put a narrow
  // no-break space before AM/PM. Both read the same once normalized.
  return new Date(sessionDate)
    .toLocaleString("en-US", { weekday: "long", hour: "numeric", minute: "2-digit" })
    .replace(" at ", " ")
    .replace(/ /g, " ")
}

export function toDraftNotice(
  session: Pick<SessionResponse, "id" | "session_date">,
  outcome: DraftOutcome,
): DraftNotice {
  return {
    sessionId: session.id,
    outcome,
    sessionLabel: draftSessionLabel(session.session_date),
  }
}

// ---------------------------------------------------------------------------
// Announced sessions. Kept in localStorage so a second tab, or the same tab
// after a reload, does not announce a draft the clinician was already told
// about. Only opaque session ids are stored. Storage can be unavailable
// (private windows, blocked site data); every access tolerates that, and the
// in-memory guard in the hook still holds within one page.
// ---------------------------------------------------------------------------

const ANNOUNCED_KEY = "pablo.draftNotices.announced"
const ANNOUNCED_LIMIT = 100

function readAnnounced(): string[] {
  try {
    const raw = window.localStorage.getItem(ANNOUNCED_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter((id) => typeof id === "string") : []
  } catch {
    return []
  }
}

export function wasAnnounced(sessionId: string): boolean {
  return readAnnounced().includes(sessionId)
}

export function markAnnounced(sessionId: string): void {
  try {
    const ids = readAnnounced().filter((id) => id !== sessionId)
    ids.push(sessionId)
    window.localStorage.setItem(
      ANNOUNCED_KEY,
      JSON.stringify(ids.slice(-ANNOUNCED_LIMIT)),
    )
  } catch {
    // Storage unavailable: the in-memory guard still prevents repeats here.
  }
}
