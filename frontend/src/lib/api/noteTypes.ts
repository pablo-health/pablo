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

/**
 * One note-type definition. Pass the `version` a note records for a
 * practice-defined type so it renders against the definition it was written
 * with; omit it for the latest (built-in types have no version).
 */
export async function getNoteType(
  key: string,
  version?: number | null,
  token?: string,
): Promise<NoteTypeSchema> {
  const query = version != null ? `?version=${version}` : ""
  return get<NoteTypeSchema>(`/api/note-types/${encodeURIComponent(key)}${query}`, token)
}
