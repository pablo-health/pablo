// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Note-Type Catalog API
 *
 * Type-safe wrappers for the `/api/note-types` registry endpoints.
 */

import type { NoteTypeListResponse, NoteTypeSchema } from "@/types/noteTypes"
import { get } from "./client"

export async function listNoteTypes(token?: string): Promise<NoteTypeListResponse> {
  return get<NoteTypeListResponse>("/api/note-types", token)
}

export async function getNoteType(
  key: string,
  token?: string,
): Promise<NoteTypeSchema> {
  return get<NoteTypeSchema>(`/api/note-types/${encodeURIComponent(key)}`, token)
}
