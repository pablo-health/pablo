// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Blank-form API functions.
 *
 * The practice's own empty paperwork — the fallback for a practice that
 * still works from paper, offered for download by a question that asks for
 * a form back. See backend/app/routes/intake_blank_forms.py. The portal's
 * download of one lives with the question that offers it, not here.
 *
 * The same two-phase signed-URL upload every other file in the system uses:
 * ask where to put it, put it there, confirm it landed. The upload step is
 * not a backend call, so it does not go through the authenticated client —
 * the URL is signed and carries its own authorization.
 */

import { del, get, post } from "./client"

const ENDPOINT = "/api/intake/blank-forms"

/** One blank form the practice can offer. */
export interface BlankForm {
  id: string
  title: string
  filename: string
  mime_type: string
  size_bytes: number
  created_at: string
}

export interface BlankFormUploadTarget {
  url: string
  method: "PUT" | "POST"
  headers: Record<string, string>
  fields: Record<string, string>
}

export interface StartedBlankFormUpload {
  form_id: string
  upload: BlankFormUploadTarget
  max_bytes: number
}

/** Every blank form still in use, newest first. */
export async function listBlankForms(token?: string): Promise<BlankForm[]> {
  return get<BlankForm[]>(ENDPOINT, token)
}

export async function startBlankFormUpload(
  input: { title: string; filename: string; mime_type: string; size_bytes: number },
  token?: string,
): Promise<StartedBlankFormUpload> {
  return post<StartedBlankFormUpload>(`${ENDPOINT}/init`, input, token)
}

export async function finishBlankFormUpload(
  formId: string,
  token?: string,
): Promise<BlankForm> {
  return post<BlankForm>(`${ENDPOINT}/${encodeURIComponent(formId)}/finalize`, {}, token)
}

export async function deleteBlankForm(
  formId: string,
  token?: string,
): Promise<{ message: string }> {
  return del<{ message: string }>(`${ENDPOINT}/${encodeURIComponent(formId)}`, token)
}

/**
 * Send the bytes to storage directly, as the target's recipe describes.
 *
 * Deliberately not through the authenticated client: the URL is signed and
 * authorizes the write on its own, and attaching a bearer token to it would
 * be sending a credential to a third party.
 */
export async function sendBlankFormToStorage(
  target: BlankFormUploadTarget,
  file: File,
): Promise<void> {
  let response: Response
  if (target.method === "POST") {
    const form = new FormData()
    for (const [name, value] of Object.entries(target.fields)) form.append(name, value)
    // The file part must come last — S3 ignores form entries after it.
    form.append("file", file)
    response = await fetch(target.url, { method: "POST", body: form })
  } else {
    response = await fetch(target.url, { method: "PUT", headers: target.headers, body: file })
  }
  if (!response.ok) {
    throw new Error(`Upload failed (${response.status})`)
  }
}
