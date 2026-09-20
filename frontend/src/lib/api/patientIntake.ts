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
 */
export interface IntakeAssignmentItem {
  id: string
  key: string
  position: number
  item_type: string
  required: boolean
  config: Record<string, unknown>
  value: Record<string, unknown> | null
}

/** One assignment, its questions, and the answers saved against them. */
export interface IntakeAssignmentDetail extends IntakeAssignment {
  items: IntakeAssignmentItem[]
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
