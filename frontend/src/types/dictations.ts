// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/** Mirrors `SessionDictationResponse`: what was dictated about a session afterwards. */
export interface SessionDictation {
  id: string
  session_id: string
  note_id: string
  status: "transcribing" | "transcribed" | "failed"
  /** Went into the note's redraft, or became a draft addendum to a signed note. */
  used_as: "redraft" | "addendum" | null
  duration_seconds: number | null
  transcript: string | null
  /** The dictation as an addendum, waiting to be reviewed and signed. */
  draft_addendum: string | null
  addendum_id: string | null
  created_at: string
  transcribed_at: string | null
}

export interface SessionDictationList {
  data: SessionDictation[]
}
