// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient portal's intake client.
 *
 * Deliberately does NOT use `get`/`post` from `@/lib/api/client`. Those fall
 * back to the signed-in clinician's Firebase ID token when no token is
 * passed, and the person filling in an intake form is not a clinician and
 * holds no Firebase session. Their principal is a portal session token, so
 * this module owns its own `fetch` and its own `Authorization` header.
 * `buildApiUrl` is shared, because where the backend lives is not this
 * module's business.
 *
 * The form is the server's. Which questions are asked, their wording and the
 * response anchors all arrive in a GET, so nothing here has a copy of them to
 * drift from the scorer.
 *
 * Nothing here takes a patient id. Every route derives it from the session
 * token, so there is no field a caller could put the wrong value in.
 *
 * **Completion is the server's too.** Every response that mentions progress
 * carries a `complete` the server computed from the rows just now, and the
 * portal renders that rather than working it out. A question somebody was
 * never shown and a question they skipped look identical from a browser.
 */

import { buildApiUrl } from "@/lib/api/client"

const FORM_PATH = "/api/patient/intake/form"
const ASSIGNMENTS_PATH = "/api/patient/intake/assignments"
const DOCUMENTS_PATH = "/api/patient/intake/documents"
const BLANK_FORMS_PATH = "/api/patient/intake/blank-forms"
const UPLOADS_PATH = "/api/patient/documents"

/** One answer choice, and the value it scores. Mirrors `IntakeResponseOptionResponse`. */
export interface IntakeResponseOption {
  value: number
  label: string
}

/** A screener as the form renders it. Mirrors `IntakeInstrumentResponse`. */
export interface IntakeInstrument {
  code: string
  display_name: string
  prompt: string
  /** Keyed by the same item keys a submission sends back. */
  items: Record<string, string>
  response_options: IntakeResponseOption[]
}

/** What the chart currently says about the person filling this in. */
export interface IntakeIdentity {
  first_name: string
  last_name: string
  date_of_birth: string | null
}

/** `GET /api/patient/intake/form`. */
export interface IntakeForm {
  identity: IntakeIdentity
  reason_prompt: string
  instruments: IntakeInstrument[]
}

/**
 * How much of a form is still outstanding. Mirrors `IntakeProgressResponse`.
 *
 * The only thing any client may believe about completion. `missing` holds
 * item ids in the order the form asks them, so the walk can send somebody to
 * the first one without sorting anything.
 */
export interface IntakeProgress {
  complete: boolean
  missing: string[]
}

/** One form somebody was asked to fill in. Mirrors `IntakeAssignmentResponse`. */
export interface IntakeAssignment {
  id: string
  version_id: string
  packet_name: string
  version: number
  status: string
  assigned_at: string
  submitted_at: string | null
  /** The code given when the form was handed in; absent until then. */
  receipt_code: string | null
  progress: IntakeProgress
}

/**
 * One question as the person answering it sees it. Mirrors
 * `IntakeAssignmentItemResponse`.
 *
 * `config` is the item type's own settings, unparsed: what a valid shape
 * looks like is the server's decision and is made there.
 *
 * `label` is the question the practice wrote and `help_text` the line under
 * it. Null on the questions the engine words for itself — those arrive in
 * the form response instead, so the wording and the scorer cannot drift.
 */
export interface IntakeAssignmentItem {
  id: string
  key: string
  position: number
  item_type: string
  required: boolean
  label: string | null
  help_text: string | null
  config: Record<string, unknown>
  value: Record<string, unknown> | null
}

/** One file attached to one question. Mirrors `IntakeArtifactResponse`. */
export interface IntakeArtifact {
  id: string
  assignment_id: string
  item_id: string
  document_id: string
  /** `"front"` or `"back"` on an insurance card, null on anything else. */
  side: string | null
  created_at: string
}

/** One assignment, its questions, and the answers saved against them. */
export interface IntakeAssignmentDetail extends IntakeAssignment {
  items: IntakeAssignmentItem[]
  /**
   * What has been sent in against the questions that asked for files.
   *
   * Rides on the assignment rather than on a route of its own, so a form
   * that asks for two photographs of a card comes back in one read knowing
   * which of them have arrived.
   */
  artifacts: IntakeArtifact[]
}

/** `PUT …/items/{item_id}` — one saved answer and what it did to the form. */
export interface SavedAnswer {
  item_id: string
  saved_at: string
  status: string
  progress: IntakeProgress
}

/** One measure a submission scored. Mirrors `SubmittedMeasureResponse`. */
export interface SubmittedMeasure {
  id: string
  instrument: string
  total_score: number | null
  severity: string | null
}

/**
 * `POST …/submit` — what handing a form in gives back.
 *
 * The totals and bands arrive because the chart records them. Nothing in the
 * portal shows either: see the receipt screen for why.
 */
export interface IntakeReceipt {
  assignment_id: string
  version_id: string
  submitted_at: string
  receipt_code: string
  measures: SubmittedMeasure[]
}

/**
 * What went wrong, at the granularity the form reacts to.
 *
 * `expired` covers both a dead session (401) and a session that never stepped
 * up (403 `STEP_UP_REQUIRED`). The portal shell owns step-up; a form mounted
 * inside it cannot raise one, so from here the two are the same dead end and
 * the same sentence.
 *
 * `invalid` (422) and `closed` (409) are the two the walk acts on rather than
 * just reports: the first means the server refused what was sent and says
 * why — this answer does not fit its question, or the form is not finished
 * and here is what is outstanding — and the second means the form stopped
 * being this patient's to change while they had it open.
 */
export type PatientIntakeErrorKind =
  | "expired"
  | "rate_limited"
  | "rejected"
  | "invalid"
  | "closed"
  | "unavailable"

export class PatientIntakeError extends Error {
  readonly kind: PatientIntakeErrorKind
  /**
   * The server's own sentence, when it sent one.
   *
   * Carried because on this surface the server writes the patient-facing
   * copy for a rejected answer — it names the question and what to do about
   * it, and it never repeats the answer back. A generic message here would
   * be less useful and no safer.
   */
  readonly serverMessage: string | null
  /** For a submit that was refused as unfinished: the item ids outstanding. */
  readonly missing: string[]

  constructor(
    kind: PatientIntakeErrorKind,
    message: string,
    options: { serverMessage?: string | null; missing?: string[] } = {},
  ) {
    super(message)
    this.name = "PatientIntakeError"
    this.kind = kind
    this.serverMessage = options.serverMessage ?? null
    this.missing = options.missing ?? []
  }
}

/**
 * The backend error envelope, which may or may not sit under `detail`.
 *
 * `{"error": {"code": …, "message": …, "details": {…}}}`, flattened to the
 * three parts a caller here branches on.
 */
interface ErrorEnvelope {
  code: string | null
  message: string | null
  missing: string[]
}

const NO_ENVELOPE: ErrorEnvelope = { code: null, message: null, missing: [] }

function envelopeOf(body: unknown): ErrorEnvelope {
  if (typeof body !== "object" || body === null) return NO_ENVELOPE
  const top = body as Record<string, unknown>
  const outer = (
    typeof top.detail === "object" && top.detail !== null ? top.detail : top
  ) as Record<string, unknown>
  const error = outer.error
  if (typeof error !== "object" || error === null) return NO_ENVELOPE
  const envelope = error as Record<string, unknown>
  const details = (
    typeof envelope.details === "object" && envelope.details !== null ? envelope.details : {}
  ) as Record<string, unknown>
  const missing = details.missing
  return {
    code: typeof envelope.code === "string" ? envelope.code : null,
    message: typeof envelope.message === "string" ? envelope.message : null,
    missing: Array.isArray(missing) ? missing.filter((id) => typeof id === "string") : [],
  }
}

async function errorFor(response: Response): Promise<PatientIntakeError> {
  let envelope = NO_ENVELOPE
  try {
    envelope = envelopeOf(await response.json())
  } catch {
    // Non-JSON body. The status still tells us everything we branch on.
  }

  if (response.status === 401) {
    return new PatientIntakeError("expired", "Session expired")
  }
  if (response.status === 403 && envelope.code === "STEP_UP_REQUIRED") {
    return new PatientIntakeError("expired", "Step-up required")
  }
  if (response.status === 429) {
    return new PatientIntakeError("rate_limited", "Too many attempts")
  }
  if (response.status === 400) {
    return new PatientIntakeError("rejected", "Submission rejected")
  }
  if (response.status === 409) {
    return new PatientIntakeError("closed", "Form closed", {
      serverMessage: envelope.message,
    })
  }
  if (response.status === 422) {
    return new PatientIntakeError("invalid", "Refused", {
      serverMessage: envelope.message,
      missing: envelope.missing,
    })
  }
  return new PatientIntakeError("unavailable", `Request failed (${response.status})`)
}

function authHeaders(sessionToken: string): Record<string, string> {
  return { Authorization: `Bearer ${sessionToken}`, Accept: "application/json" }
}

/** A network failure reads the same as a server that isn't answering. */
function unavailable(): PatientIntakeError {
  return new PatientIntakeError("unavailable", "Could not reach the server")
}

/**
 * One request, with the session on it, decoded or turned into our own error.
 *
 * Every call below is the same three lines, so they are here once: a network
 * failure and a refusing server read the same way, and a body is only parsed
 * after the status says there is one worth parsing.
 */
async function request<T>(
  sessionToken: string,
  path: string,
  init: { method: string; body?: unknown } = { method: "GET" },
): Promise<T> {
  let response: Response
  try {
    response = await fetch(buildApiUrl(path), {
      method: init.method,
      headers:
        init.body === undefined
          ? authHeaders(sessionToken)
          : { ...authHeaders(sessionToken), "Content-Type": "application/json" },
      ...(init.body === undefined ? {} : { body: JSON.stringify(init.body) }),
    })
  } catch {
    throw unavailable()
  }
  if (!response.ok) throw await errorFor(response)
  return (await response.json()) as T
}

/**
 * Fetch the wording the engine owns: the caller's own name and date of
 * birth, the "what brings you in" prompt, and each measure's items and
 * anchors.
 *
 * A form's *shape* is the practice's and arrives with the assignment; the
 * wording of the questions the engine asks itself is the engine's, and it
 * arrives here so the portal and the scorer cannot drift.
 */
export async function fetchIntakeForm(sessionToken: string): Promise<IntakeForm> {
  return request<IntakeForm>(sessionToken, FORM_PATH)
}

/** The forms this patient has been asked to fill in, newest first. */
export async function listAssignments(sessionToken: string): Promise<IntakeAssignment[]> {
  return request<IntakeAssignment[]>(sessionToken, ASSIGNMENTS_PATH)
}

/** One form, its questions in order, and whatever has been saved so far. */
export async function fetchAssignment(
  sessionToken: string,
  assignmentId: string,
): Promise<IntakeAssignmentDetail> {
  return request<IntakeAssignmentDetail>(
    sessionToken,
    `${ASSIGNMENTS_PATH}/${encodeURIComponent(assignmentId)}`,
  )
}

/**
 * Save one answer.
 *
 * Idempotent server-side: the same question saved twice updates one row, so
 * a retry after a dropped connection is safe.
 */
export async function saveAnswer(
  sessionToken: string,
  assignmentId: string,
  itemId: string,
  value: Record<string, unknown>,
): Promise<SavedAnswer> {
  return request<SavedAnswer>(
    sessionToken,
    `${ASSIGNMENTS_PATH}/${encodeURIComponent(assignmentId)}/items/${encodeURIComponent(itemId)}`,
    { method: "PUT", body: { value } },
  )
}

/**
 * One published consent document, as the person being asked to sign sees it.
 * Mirrors `PatientDocumentResponse`.
 *
 * `rendered_html` was built by the server from the markdown a practice typed,
 * by escaping first and emitting a fixed set of tags second — so there is no
 * character the practice could type that reaches the browser as markup. That
 * is why this is the one place in the portal that sets inner HTML.
 *
 * No `body_markdown`: the rendered words are what is read, and the digest is
 * taken over those words rather than over their source.
 */
export interface PatientConsentDocument {
  id: string
  document_key: string
  title: string
  rendered_html: string
  version: number
  digest: string
  requires_signature: boolean
  /** Who this document asks to sign: the patient, and sometimes a guardian. */
  signer_roles: string[]
  /**
   * The sentence a signature taken now would be agreed under.
   *
   * Served rather than written here, because its version is recorded on the
   * signature — a copy in this module would be free to drift from what a
   * stored signature says was agreed.
   */
  consent_statement: string
  consent_statement_version: string
}

/** `POST …/signatures` — what was recorded. Mirrors `IntakeSignatureResponse`. */
export interface IntakeSignature {
  id: string
  assignment_id: string
  item_id: string
  document_version_id: string
  document_digest: string
  signer_role: string
  signer_typed_name: string
  consent_statement_version: string
  /** The sentence that was agreed under, resolved from the stored version. */
  consent_statement: string
  signed_at: string
  auth_strength: string
  session_id: string | null
  evidence_digest: string
}

/** The document somebody is about to sign, by the version the form pinned. */
export async function fetchConsentDocument(
  sessionToken: string,
  documentId: string,
): Promise<PatientConsentDocument> {
  return request<PatientConsentDocument>(
    sessionToken,
    `${DOCUMENTS_PATH}/${encodeURIComponent(documentId)}`,
  )
}

/**
 * What this patient has already signed on one form, oldest first.
 *
 * Read from the server rather than remembered by the browser that took the
 * signature: a form somebody signed yesterday on another device still has to
 * look signed today.
 */
export async function listSignatures(
  sessionToken: string,
  assignmentId: string,
): Promise<IntakeSignature[]> {
  return request<IntakeSignature[]>(
    sessionToken,
    `${ASSIGNMENTS_PATH}/${encodeURIComponent(assignmentId)}/signatures`,
  )
}

/**
 * Type a name against one of the consent documents on a form.
 *
 * Not idempotent, unlike saving an answer, and deliberately so: signing twice
 * is refused with a 409 rather than quietly returning the first signature.
 * Two agreements minutes apart are two events, and the server is what decides
 * whether the second one is real.
 */
export async function signConsentDocument(
  sessionToken: string,
  assignmentId: string,
  body: { item_id: string; signer_role: string; typed_name: string; affirm: boolean },
): Promise<IntakeSignature> {
  return request<IntakeSignature>(
    sessionToken,
    `${ASSIGNMENTS_PATH}/${encodeURIComponent(assignmentId)}/signatures`,
    { method: "POST", body },
  )
}

/** Hand the form in, and get the receipt back. */
export async function submitAssignment(
  sessionToken: string,
  assignmentId: string,
): Promise<IntakeReceipt> {
  return request<IntakeReceipt>(
    sessionToken,
    `${ASSIGNMENTS_PATH}/${encodeURIComponent(assignmentId)}/submit`,
    { method: "POST" },
  )
}

// ---------------------------------------------------------------------------
// The files a form asked for
// ---------------------------------------------------------------------------
//
// Three calls in a row, and the order is the whole design. The browser asks
// for somewhere to put a file, puts it there, and only then tells the form
// which question it answers. Nothing the browser sends becomes the question's
// answer — the server writes that from the rows it has just checked.

/** `POST /api/patient/documents/init` — where to put a file. */
export interface PatientUploadTarget {
  url: string
  method: "PUT" | "POST"
  headers: Record<string, string>
  fields: Record<string, string>
}

export interface StartedUpload {
  document_id: string
  upload: PatientUploadTarget
  /** For pre-flight only; the storage layer enforces the cap. */
  max_bytes: number
}

/** What was attached, and where the form stands afterwards. */
export interface ArtifactWrite {
  artifact: IntakeArtifact
  status: string
  progress: IntakeProgress
}

/** Ask for somewhere to put a file this form asked for. */
export async function startUpload(
  sessionToken: string,
  file: { name: string; type: string; size: number },
): Promise<StartedUpload> {
  return request<StartedUpload>(sessionToken, `${UPLOADS_PATH}/init`, {
    method: "POST",
    body: {
      filename: file.name,
      mime_type: file.type,
      size_bytes: file.size,
      category: "intake_artifact",
    },
  })
}

/**
 * Send the bytes to storage directly, as the target's recipe describes.
 *
 * Not an API call, so it does not go through `request`: the URL is signed
 * and carries its own authorization, and attaching a session token to it
 * would be sending a credential to a third party.
 */
export async function sendToStorage(target: PatientUploadTarget, file: File): Promise<void> {
  let response: Response
  try {
    if (target.method === "POST") {
      const form = new FormData()
      for (const [name, value] of Object.entries(target.fields)) form.append(name, value)
      // The file part must come last — S3 ignores form entries after it.
      form.append("file", file)
      response = await fetch(target.url, { method: "POST", body: form })
    } else {
      response = await fetch(target.url, {
        method: "PUT",
        headers: target.headers,
        body: file,
      })
    }
  } catch {
    throw unavailable()
  }
  if (!response.ok) {
    throw new PatientIntakeError("unavailable", `Upload failed (${response.status})`)
  }
}

/**
 * Confirm the upload finished and put the file on the chart.
 *
 * The 422 this can raise is the type check: the server reads the stored
 * file's first bytes, and a file that is not the kind of file it said it
 * was is refused here rather than on the chart.
 */
export async function finishUpload(
  sessionToken: string,
  documentId: string,
): Promise<{ id: string }> {
  return request<{ id: string }>(
    sessionToken,
    `${UPLOADS_PATH}/${encodeURIComponent(documentId)}/finalize`,
    { method: "POST" },
  )
}

/** Say which question a finished upload answers. */
export async function attachArtifact(
  sessionToken: string,
  assignmentId: string,
  body: { item_id: string; document_id: string; side?: string },
): Promise<ArtifactWrite> {
  return request<ArtifactWrite>(
    sessionToken,
    `${ASSIGNMENTS_PATH}/${encodeURIComponent(assignmentId)}/artifacts`,
    { method: "POST", body },
  )
}

/** Take a file back off a form that has not been handed in. */
export async function removeArtifact(
  sessionToken: string,
  assignmentId: string,
  artifactId: string,
): Promise<ArtifactWrite> {
  return request<ArtifactWrite>(
    sessionToken,
    `${ASSIGNMENTS_PATH}/${encodeURIComponent(assignmentId)}/artifacts/` +
      encodeURIComponent(artifactId),
    { method: "DELETE" },
  )
}

/** A short-lived URL for one of this patient's own uploads, to preview it. */
export async function uploadPreviewUrl(
  sessionToken: string,
  documentId: string,
): Promise<string> {
  const { url } = await request<{ url: string }>(
    sessionToken,
    `${UPLOADS_PATH}/${encodeURIComponent(documentId)}/file?disposition=inline`,
  )
  return url
}

/** A short-lived URL for one of the practice's own blank forms. */
export async function blankFormUrl(
  sessionToken: string,
  blankFormId: string,
): Promise<string> {
  const { url } = await request<{ url: string }>(
    sessionToken,
    `${BLANK_FORMS_PATH}/${encodeURIComponent(blankFormId)}/file`,
  )
  return url
}

/** The plan written on an insurance card, as the patient types it. */
export interface IntakeCoverageFields {
  payer_name: string
  payer_id?: string | null
  member_id: string
  group_number?: string | null
  plan_name?: string | null
  subscriber_relationship?: string
  subscriber_first_name?: string | null
  subscriber_last_name?: string | null
  subscriber_date_of_birth?: string | null
}

/**
 * `PUT …/items/{item_id}/coverage` — what typing the plan did.
 *
 * `eligibility_requested` says whether a check was queued with the payer.
 * It is deliberately not a claim about the answer: nothing on the screen
 * waits for one.
 */
export interface SavedIntakeCoverage {
  coverage_id: string
  eligibility_requested: boolean
}

/** Put the plan written on the card on file. */
export async function saveIntakeCoverage(
  sessionToken: string,
  assignmentId: string,
  itemId: string,
  fields: IntakeCoverageFields,
): Promise<SavedIntakeCoverage> {
  return request<SavedIntakeCoverage>(
    sessionToken,
    `${ASSIGNMENTS_PATH}/${encodeURIComponent(assignmentId)}/items/` +
      `${encodeURIComponent(itemId)}/coverage`,
    { method: "PUT", body: fields },
  )
}
