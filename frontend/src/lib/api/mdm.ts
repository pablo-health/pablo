// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { MdmChoicesRequest, MdmReview } from "@/types/mdm"
import { get, post, put } from "./client"

export async function getMdmReview(noteId: string, token?: string): Promise<MdmReview> {
  return get<MdmReview>(`/api/notes/${noteId}/mdm`, token)
}

export async function putMdmChoices(
  noteId: string,
  data: MdmChoicesRequest,
  token?: string,
): Promise<MdmReview> {
  return put<MdmReview>(`/api/notes/${noteId}/mdm`, data, token)
}

/** Puts the computed codes in the note's visit details, as the clinician's edit. */
export async function applyMdmCodes(noteId: string, token?: string): Promise<MdmReview> {
  return post<MdmReview>(`/api/notes/${noteId}/mdm/apply`, {}, token)
}
