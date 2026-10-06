// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Dictating more about a session after its recording stopped.
 *
 * The clip is taken at once (202); transcribing it and applying it run off
 * the request. An unsigned note is redrafted with it — poll the session for
 * the note's status. A signed note gets a draft addendum — poll the list for
 * `draft_addendum`, then sign it with `addNoteAddendum(..., { dictation_id })`.
 */

import type { RedraftEdits } from "@/types/notes"
import type { SessionDictation, SessionDictationList } from "@/types/dictations"
import { get, postForm } from "./client"

export async function addSessionDictation(
  sessionId: string,
  audio: Blob,
  options: { durationSeconds?: number; edits?: RedraftEdits },
  token?: string,
): Promise<SessionDictation> {
  const form = new FormData()
  const extension = audio.type.includes("mp4") ? "m4a" : "webm"
  form.append("audio", audio, `dictation.${extension}`)
  if (options.durationSeconds !== undefined) {
    form.append("duration_seconds", String(Math.round(options.durationSeconds)))
  }
  if (options.edits) form.append("edits", options.edits)
  return postForm<SessionDictation>(`/api/sessions/${sessionId}/dictations`, form, token)
}

export async function listSessionDictations(
  sessionId: string,
  token?: string,
): Promise<SessionDictationList> {
  return get<SessionDictationList>(`/api/sessions/${sessionId}/dictations`, token)
}
