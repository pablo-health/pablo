// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient documents API client (THERAPY-ak6m.2).
 *
 * Two-phase signed-URL upload:
 *
 *   1. POST /api/patients/{id}/documents/init  -> {upload_url, document_id}
 *   2. PUT {upload_url} (browser -> GCS direct, no backend buffer)
 *   3. POST /api/documents/{document_id}/finalize -> verified + extracted
 *
 * The browser PUT step is _not_ a backend call, so it doesn't go through
 * apiClient. It needs the exact Content-Type the URL was signed with and
 * the `x-goog-content-length-range` header that mirrors the size cap.
 */

import { del, get, post } from "./client"
import type {
  DeleteDocumentResponse,
  DocumentDownloadUrlResponse,
  InitUploadRequest,
  InitUploadResponse,
  PatientDocumentListResponse,
  PatientDocumentResponse,
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
 * Browser-direct GCS upload via the signed PUT URL.
 *
 * Throws if the PUT response status isn't 2xx. The `x-goog-content-length-range`
 * header value mirrors what the backend signed: GCS enforces both bounds
 * so a tampered Content-Type or oversize body is rejected at GCS, not in
 * our backend.
 */
export async function uploadFileToSignedUrl(
  signedUrl: string,
  file: File,
  maxBytes: number,
  contentType: string,
): Promise<void> {
  const response = await fetch(signedUrl, {
    method: "PUT",
    headers: {
      "Content-Type": contentType,
      "x-goog-content-length-range": `0,${maxBytes}`,
    },
    body: file,
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => "")
    throw new Error(`GCS upload failed (${response.status}): ${detail}`)
  }
}

/**
 * Fetches a short-lived signed GCS download URL through the authenticated
 * API client (bearer token attached), mirroring the upload path's signed
 * PUT URL. The returned URL is GCS-signed, so the caller navigates to it
 * directly with no auth header. A raw `<a href>` to `/file` can't carry
 * our bearer token and 401s (PABLO-47h).
 */
export async function getPatientDocumentDownloadUrl(
  documentId: string,
  token?: string,
): Promise<string> {
  const { url } = await get<DocumentDownloadUrlResponse>(
    `/api/documents/${documentId}/file`,
    token,
  )
  return url
}
