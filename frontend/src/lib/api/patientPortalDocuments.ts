// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient-side client for the documents a patient sends in themselves.
 *
 * A bare `fetch` on a patient session token, for the same reason
 * `patientMessages.ts` is one: the clinician API client resolves its
 * credential from a signed-in clinician, and on this surface there is no
 * such user. Nothing here takes a patient id — the routes derive it from
 * the token, so there is no field for a caller to put the wrong value in.
 *
 * The upload itself does not come through here. The backend answers `init`
 * with a self-describing recipe and the browser executes it against storage
 * directly, which is what keeps a large file off the API process entirely;
 * `uploadFileToStorage` runs that recipe and is shared with the clinician
 * client rather than written twice.
 *
 * Failures carry a status and nothing else. A filename is PHI-adjacent and
 * an error that quoted one would put it into console output and error
 * reporting, where it does not belong.
 */

import { buildApiUrl } from "@/lib/api/client"
import { uploadFileToStorage } from "@/lib/api/patientDocuments"
import type {
  InitUploadResponse,
  PatientDocumentResponse,
} from "@/types/patientDocuments"

const BASE = "/api/patient/documents"

/**
 * What a patient may file a document as. The backend request model has no
 * default here on purpose — the two values are the two reasons the surface
 * exists, so naming one is no burden and a silent default would file an
 * insurance card as correspondence.
 */
export type PatientUploadCategory = "intake_artifact" | "message"

/** A failed request. Carries the status and no part of the payload. */
export class PatientDocumentsError extends Error {
  constructor(public status: number) {
    super(`Patient document request failed (${status})`)
    this.name = "PatientDocumentsError"
  }
}

function headers(sessionToken: string): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${sessionToken}`,
  }
}

async function request<T>(
  sessionToken: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(buildApiUrl(`${BASE}${path}`), {
    ...init,
    headers: headers(sessionToken),
  })
  if (!response.ok) throw new PatientDocumentsError(response.status)
  return (await response.json()) as T
}

/** Ask for somewhere to put a file, and get the recipe for putting it there. */
export async function initOwnDocumentUpload(
  sessionToken: string,
  input: {
    filename: string
    mimeType: string
    sizeBytes: number
    category: PatientUploadCategory
  },
): Promise<InitUploadResponse> {
  return request<InitUploadResponse>(sessionToken, "/init", {
    method: "POST",
    body: JSON.stringify({
      filename: input.filename,
      mime_type: input.mimeType,
      size_bytes: input.sizeBytes,
      category: input.category,
    }),
  })
}

/** Confirm the upload finished. Until this succeeds the file is not on the chart. */
export async function finalizeOwnDocumentUpload(
  sessionToken: string,
  documentId: string,
): Promise<PatientDocumentResponse> {
  return request<PatientDocumentResponse>(
    sessionToken,
    `/${encodeURIComponent(documentId)}/finalize`,
    { method: "POST" },
  )
}

/**
 * A short-lived signed URL for one of the patient's own documents.
 *
 * The signature authorizes the fetch on its own, so the caller navigates to
 * the returned URL directly — a raw `<a href>` to the route cannot carry the
 * session token.
 */
export async function getOwnDocumentDownloadUrl(
  sessionToken: string,
  documentId: string,
  disposition: "attachment" | "inline" = "attachment",
): Promise<string> {
  const { url } = await request<{ url: string }>(
    sessionToken,
    `/${encodeURIComponent(documentId)}/file?disposition=${disposition}`,
  )
  return url
}

/**
 * The whole three-step send of one file: init, upload, finalize.
 *
 * Returns the finalized document, which is the only state a message may
 * attach — an interrupted upload leaves a row nothing can reach and no
 * caller has to clean it up.
 */
export async function uploadOwnDocument(
  sessionToken: string,
  file: File,
  category: PatientUploadCategory,
): Promise<PatientDocumentResponse> {
  const started = await initOwnDocumentUpload(sessionToken, {
    filename: file.name,
    mimeType: file.type,
    sizeBytes: file.size,
    category,
  })
  await uploadFileToStorage(started.upload, file)
  return finalizeOwnDocumentUpload(sessionToken, started.document_id)
}
