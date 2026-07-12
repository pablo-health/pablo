// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient documents API client (THERAPY-ak6m.2).
 *
 * Two-phase signed-URL upload:
 *
 *   1. POST /api/patients/{id}/documents/init -> {document_id, upload}
 *   2. Execute the `upload` recipe (browser -> storage direct, no
 *      backend buffer; PUT or form-POST per the configured provider)
 *   3. POST /api/documents/{document_id}/finalize -> verified + extracted
 *
 * The browser upload step is _not_ a backend call, so it doesn't go
 * through apiClient. See uploadFileToStorage.
 */

import { del, get, post } from "./client"
import type {
  DeleteDocumentResponse,
  DocumentDownloadUrlResponse,
  InitUploadRequest,
  InitUploadResponse,
  PatientDocumentListResponse,
  PatientDocumentResponse,
  UploadTarget,
} from "@/types/patientDocuments"

export async function initPatientDocumentUpload(
  patientId: string,
  body: InitUploadRequest,
  token?: string,
): Promise<InitUploadResponse> {
  return post<InitUploadResponse>(
    `/api/patients/${patientId}/documents/init`,
    body,
    token,
  )
}

export async function finalizePatientDocumentUpload(
  documentId: string,
  token?: string,
): Promise<PatientDocumentResponse> {
  return post<PatientDocumentResponse>(
    `/api/documents/${documentId}/finalize`,
    {},
    token,
  )
}

export async function listPatientDocuments(
  patientId: string,
  token?: string,
): Promise<PatientDocumentListResponse> {
  return get<PatientDocumentListResponse>(
    `/api/patients/${patientId}/documents`,
    token,
  )
}

export async function getPatientDocument(
  documentId: string,
  token?: string,
): Promise<PatientDocumentResponse> {
  return get<PatientDocumentResponse>(`/api/documents/${documentId}`, token)
}

export async function deletePatientDocument(
  documentId: string,
  token?: string,
): Promise<DeleteDocumentResponse> {
  return del<DeleteDocumentResponse>(`/api/documents/${documentId}`, token)
}

/**
 * Browser-direct upload: execute the target's recipe verbatim.
 *
 * The backend's storage provider fully specifies the request (see
 * UploadTarget) — the signed content-type/size constraints ride in
 * `headers` (PUT) or `fields` (POST), so the storage service rejects
 * anything tampered or oversized. Throws if the status isn't 2xx.
 */
export async function uploadFileToStorage(
  target: UploadTarget,
  file: File,
): Promise<void> {
  let response: Response
  if (target.method === "POST") {
    const form = new FormData()
    for (const [name, value] of Object.entries(target.fields)) {
      form.append(name, value)
    }
    // The file part must come last — S3 ignores form entries after it.
    form.append("file", file)
    // No explicit headers: the browser sets the multipart boundary.
    response = await fetch(target.url, { method: "POST", body: form })
  } else {
    response = await fetch(target.url, {
      method: "PUT",
      headers: target.headers,
      body: file,
    })
  }
  if (!response.ok) {
    const detail = await response.text().catch(() => "")
    throw new Error(`Storage upload failed (${response.status}): ${detail}`)
  }
}

/**
 * Fetches a short-lived signed GCS download URL through the authenticated
 * API client (bearer token attached), mirroring the upload path's signed
 * PUT URL. The returned URL is GCS-signed, so the caller navigates to it
 * directly with no auth header. A raw `<a href>` to `/file` can't carry
 * our bearer token and 401s (PABLO-47h).
 *
 * `disposition` controls how the browser treats the URL: `attachment`
 * (default) forces a download with a friendly filename; `inline` lets the
 * in-app viewer render PDFs/images in place (PABLO-6x5.3).
 */
export async function getPatientDocumentDownloadUrl(
  documentId: string,
  token?: string,
  disposition: "attachment" | "inline" = "attachment",
): Promise<string> {
  const { url } = await get<DocumentDownloadUrlResponse>(
    `/api/documents/${documentId}/file?disposition=${disposition}`,
    token,
  )
  return url
}
