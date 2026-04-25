// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Note-Type catalog types
 *
 * Mirrors the backend `NoteTypeRegistry` shape exposed at
 * `GET /api/note-types`. The registry is the source of truth for which
 * note types the user can pick when starting a session.
 */

export type NoteFieldKind = "text" | "list" | "structured"

export interface NoteFieldSchema {
  key: string
  label: string
  kind: NoteFieldKind
  ai_hint: string
}

export interface NoteSectionSchema {
  key: string
  label: string
  fields: NoteFieldSchema[]
}

export interface NoteTypeSchema {
  key: string
  label: string
  description: string
  tier: "oss" | "saas"
  sections: NoteSectionSchema[]
}

export interface NoteTypeListResponse {
  note_types: NoteTypeSchema[]
}

export const DEFAULT_NOTE_TYPE = "soap"
