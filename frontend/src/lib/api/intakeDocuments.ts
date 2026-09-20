// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Consent document API functions.
 *
 * The practice writing the documents it asks people to sign. See
 * backend/app/routes/intake_documents.py. The portal's read of a published
 * document lives with the signing screen, not here.
 */

import type {
  CreateDocumentInput,
  IntakeDocument,
  UpdateDocumentInput,
} from "@/types/intakeDocuments"
import { get, post, put } from "./client"

const ENDPOINT = "/api/intake/documents"

/**
 * Every document the practice has written, newest version of each.
 *
 * `publishedOnly` asks a different question: the newest PUBLISHED version
 * of each, which is what a form may ask somebody to sign. The two differ
 * for a document somebody is midway through revising, so the form editor
 * asks the server rather than filtering the default list.
 */
export async function listIntakeDocuments(
  options: { publishedOnly?: boolean } = {},
  token?: string
): Promise<IntakeDocument[]> {
  const query = options.publishedOnly ? "?published_only=true" : ""
  return get<IntakeDocument[]>(`${ENDPOINT}${query}`, token)
}

export async function createIntakeDocument(
  input: CreateDocumentInput,
  token?: string
): Promise<IntakeDocument> {
  return post<IntakeDocument>(ENDPOINT, input, token)
}

export async function updateIntakeDocument(
  documentId: string,
  input: UpdateDocumentInput,
  token?: string
): Promise<IntakeDocument> {
  return put<IntakeDocument>(`${ENDPOINT}/${documentId}`, input, token)
}

export async function publishIntakeDocument(
  documentId: string,
  token?: string
): Promise<IntakeDocument> {
  return post<IntakeDocument>(`${ENDPOINT}/${documentId}/publish`, {}, token)
}

/**
 * Start a draft from this document's latest text. A document that already
 * has an unpublished draft hands that one back rather than stacking a second.
 */
export async function createIntakeDocumentVersion(
  documentId: string,
  token?: string
): Promise<IntakeDocument> {
  return post<IntakeDocument>(`${ENDPOINT}/${documentId}/new-version`, {}, token)
}
