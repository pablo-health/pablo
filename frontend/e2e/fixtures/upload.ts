// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Send a file the way a patient's browser does: ask the API for an upload
 * target, send the bytes to storage directly, then tell the API it landed.
 *
 * Factored out because nothing in a spec should have to know which storage
 * backend the deployment runs. The API answers with a self-describing
 * recipe — a signed PUT with headers, or a signed multipart POST with
 * policy fields — and the sender below executes whichever it is given. A
 * spec that switches store therefore does not change.
 *
 * Reading a file's bytes here rather than pointing at a path is deliberate:
 * the same buffer is hashed and sent, so a round-trip comparison is against
 * what actually left, not against a second read of the disk.
 */

import { createHash } from "node:crypto"
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import type { APIRequestContext } from "@playwright/test"
import { expect } from "@playwright/test"
import { BACKEND_URL } from "./stack"

/** The two categories a patient's own upload may be filed under. */
export type PatientUploadCategory = "intake_artifact" | "message"

/** A file to send, already in memory. */
export interface UploadFile {
  name: string
  mimeType: string
  body: Buffer
}

/**
 * The API's upload recipe, mirroring `UploadTarget` in
 * `backend/app/services/file_storage.py`.
 */
export interface UploadTarget {
  url: string
  method: "PUT" | "POST"
  headers: Record<string, string>
  fields: Record<string, string>
}

interface InitUploadResponse {
  document_id: string
  upload: UploadTarget
  max_bytes: number
}

/** Read one of the committed fixture files in `fixtures/files/`. */
export function fixtureFile(name: string, mimeType: string): UploadFile {
  const path = fileURLToPath(new URL(`./files/${name}`, import.meta.url))
  return { name, mimeType, body: readFileSync(path) }
}

/** The hash a round trip has to reproduce. */
export function sha256(body: Buffer): string {
  return createHash("sha256").update(body).digest("hex")
}

/**
 * Execute an upload recipe against the store and return its response status.
 *
 * `PUT` sends the raw body with the signed headers attached. `POST` sends
 * multipart form data with the signed policy fields ahead of the file part
 * — ahead, because a storage policy is evaluated against the fields it has
 * already seen when the file arrives, so a file part sent first is refused.
 * Returned rather than asserted: a spec that is proving the store refuses
 * something needs the status, not a failure here.
 */
export async function sendToUploadTarget(
  request: APIRequestContext,
  target: UploadTarget,
  file: UploadFile,
): Promise<number> {
  if (target.method === "PUT") {
    const sent = await request.put(target.url, { headers: target.headers, data: file.body })
    return sent.status()
  }
  const sent = await request.post(target.url, {
    multipart: {
      ...target.fields,
      file: { name: file.name, mimeType: file.mimeType, buffer: file.body },
    },
  })
  return sent.status()
}

export interface UploadedDocument {
  id: string
  /** The hash of the bytes that were sent, for comparing a download against. */
  sha256: string
}

/**
 * Upload a file as the signed-in patient, all three steps, and assert each.
 *
 * `sessionToken` is what `givePortalSession` hands back. The document is on
 * the chart when this returns: finalize is what puts it there, and an
 * upload that is started and never finalized is visible to nobody.
 */
export async function uploadAsPatient(
  request: APIRequestContext,
  sessionToken: string,
  file: UploadFile,
  category: PatientUploadCategory,
): Promise<UploadedDocument> {
  const headers = { Authorization: `Bearer ${sessionToken}` }

  const started = await request.post(`${BACKEND_URL}/api/patient/documents/init`, {
    headers,
    data: {
      filename: file.name,
      mime_type: file.mimeType,
      size_bytes: file.body.length,
      category,
    },
  })
  expect(started.status(), `the API mints an upload target for ${file.name}`).toBe(201)
  const init = (await started.json()) as InitUploadResponse

  const stored = await sendToUploadTarget(request, init.upload, file)
  expect(stored, `the store accepts ${file.name}`).toBeLessThan(300)

  const finalized = await request.post(
    `${BACKEND_URL}/api/patient/documents/${init.document_id}/finalize`,
    { headers },
  )
  expect(finalized.status(), `the upload finalizes onto the chart`).toBe(200)

  return { id: init.document_id, sha256: sha256(file.body) }
}
