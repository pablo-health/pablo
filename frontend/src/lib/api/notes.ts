// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Notes API client
 *
 * Type-safe wrappers around ``/api/notes`` and the standalone-note path
 * ``/api/patients/{patient_id}/notes`` (pa-0nx.2 + pa-0nx.3).
 */

import type {
  CreateStandaloneNoteRequest,
  FinalizeNoteRequest,
  Note,
  PatientNotesListResponse,
  UpdateNoteEditsRequest,
} from "@/types/notes"
import { get, patch, post } from "./client"

export async function fetchNote(noteId: string, token?: string): Promise<Note> {
  return get<Note>(`/api/notes/${noteId}`, token)
}

export async function updateNoteEdits(
  noteId: string,
  data: UpdateNoteEditsRequest,
  token?: string,
): Promise<Note> {
  return patch<Note>(`/api/notes/${noteId}`, data, token)
}

export async function finalizeNote(
  noteId: string,
  data: FinalizeNoteRequest,
  token?: string,
): Promise<Note> {
  return post<Note>(`/api/notes/${noteId}/finalize`, data, token)
}

export async function createStandaloneNote(
  patientId: string,
  data: CreateStandaloneNoteRequest,
  token?: string,
): Promise<Note> {
  return post<Note>(`/api/patients/${patientId}/notes`, data, token)
}

export async function listNotesForPatient(
  patientId: string,
  token?: string,
): Promise<PatientNotesListResponse> {
  return get<PatientNotesListResponse>(`/api/patients/${patientId}/notes`, token)
}
