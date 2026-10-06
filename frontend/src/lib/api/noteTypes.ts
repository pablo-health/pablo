// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Note-Type Catalog API
 *
 * Type-safe wrappers for the `/api/note-types` registry endpoints.
 */

import type {
  DeriveNoteTypeRequest,
  DeriveNoteTypeResponse,
  NoteDraftPreviewRequest,
  NoteDraftPreviewResponse,
  NoteTypeListResponse,
  NoteTypeReferenceListResponse,
  NoteTypeSchema,
  PracticeNoteTypeSpec,
} from "@/types/noteTypes"
import { del, get, post, postForm, put } from "./client"

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

/** Save the next version of the practice's type `custom.<slug>`. */
export async function savePracticeNoteType(
  slug: string,
  spec: PracticeNoteTypeSpec,
  token?: string,
): Promise<NoteTypeSchema> {
  return put<NoteTypeSchema>(`/api/note-types/custom/${encodeURIComponent(slug)}`, spec, token)
}

/** Retire `custom.<slug>`: new notes can't use it; notes written with it still render. */
export async function retirePracticeNoteType(slug: string, token?: string): Promise<NoteTypeSchema> {
  return del<NoteTypeSchema>(`/api/note-types/custom/${encodeURIComponent(slug)}`, token)
}

/** Draft a note of one type from a transcript, without saving anything. */
export async function previewNoteDraft(
  body: NoteDraftPreviewRequest,
  token?: string,
): Promise<NoteDraftPreviewResponse> {
  return post<NoteDraftPreviewResponse>("/api/note-types/preview", body, token)
}

/**
 * Propose a note type from sample notes and/or a description. Nothing is
 * saved; saving is `savePracticeNoteType` with the returned spec.
 */
export async function deriveNoteType(
  request: DeriveNoteTypeRequest,
  token?: string,
): Promise<DeriveNoteTypeResponse> {
  const form = new FormData()
  for (const sample of request.samples) form.append("samples", sample)
  for (const file of request.files) form.append("files", file, file.name)
  if (request.description.trim()) form.append("description", request.description)
  if (request.reference) form.append("reference", request.reference)
  return postForm<DeriveNoteTypeResponse>("/api/note-types/derive", form, token)
}

/** References this deployment registered to compare a proposal against, besides the note types. */
export async function listDeriveReferences(token?: string): Promise<NoteTypeReferenceListResponse> {
  return get<NoteTypeReferenceListResponse>("/api/note-types/derive/references", token)
}
