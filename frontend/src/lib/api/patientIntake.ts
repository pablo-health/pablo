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
 * The form is the server's. Item wording, the response anchors and which
 * screeners are asked all arrive in the GET response, so nothing here has a
 * copy of them to drift from the scorer. A submission names items by key and
 * value, and carries no patient id at all — the server reads that off the
 * session.
 */

import { buildApiUrl } from "@/lib/api/client"

const FORM_PATH = "/api/patient/intake/form"
const SUBMISSIONS_PATH = "/api/patient/intake/submissions"

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
 * `POST /api/patient/intake/submissions` body.
 *
 * `name_confirmed` and `dob_confirmed` are attestations: a "no" is recorded
 * beside `corrections` for the clinician to read, and never blocks the
 * submission.
 */
export interface SubmitIntakeRequest {
  name_confirmed: boolean
  dob_confirmed: boolean
  corrections: string | null
  reason_text: string
  phq9: Record<string, number>
  gad7: Record<string, number>
}

/** One scored screener, as recorded. */
export interface IntakeMeasure {
  id: string
  instrument: string
  total_score: number | null
  severity: string | null
}

/** `POST /api/patient/intake/submissions` — 201. */
export interface IntakeSubmission {
  id: string
  submitted_at: string
  measures: IntakeMeasure[]
}

/**
 * What went wrong, at the granularity the form reacts to.
 *
 * `expired` covers both a dead session (401) and a session that never stepped
 * up (403 `STEP_UP_REQUIRED`). The portal shell owns step-up; a form mounted
 * inside it cannot raise one, so from here the two are the same dead end and
 * the same sentence.
 */
export type PatientIntakeErrorKind =
  | "expired"
  | "rate_limited"
  | "rejected"
  | "unavailable"

export class PatientIntakeError extends Error {
  readonly kind: PatientIntakeErrorKind

  constructor(kind: PatientIntakeErrorKind, message: string) {
    super(message)
    this.name = "PatientIntakeError"
    this.kind = kind
  }
}

/** The backend error envelope, which may or may not sit under `detail`. */
function errorCodeOf(body: unknown): string | null {
  if (typeof body !== "object" || body === null) return null
  const top = body as Record<string, unknown>
  const envelope = (
    typeof top.detail === "object" && top.detail !== null ? top.detail : top
  ) as Record<string, unknown>
  const error = envelope.error
  if (typeof error !== "object" || error === null) return null
  const code = (error as Record<string, unknown>).code
  return typeof code === "string" ? code : null
}

async function errorFor(response: Response): Promise<PatientIntakeError> {
  let code: string | null = null
  try {
    code = errorCodeOf(await response.json())
  } catch {
    // Non-JSON body. The status still tells us everything we branch on.
  }

  if (response.status === 401) {
    return new PatientIntakeError("expired", "Session expired")
  }
  if (response.status === 403 && code === "STEP_UP_REQUIRED") {
    return new PatientIntakeError("expired", "Step-up required")
  }
  if (response.status === 429) {
    return new PatientIntakeError("rate_limited", "Too many attempts")
  }
  if (response.status === 400) {
    return new PatientIntakeError("rejected", "Submission rejected")
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

/** Fetch the intake form for whoever holds this portal session. */
export async function fetchIntakeForm(sessionToken: string): Promise<IntakeForm> {
  let response: Response
  try {
    response = await fetch(buildApiUrl(FORM_PATH), {
      method: "GET",
      headers: authHeaders(sessionToken),
    })
  } catch {
    throw unavailable()
  }
  if (!response.ok) throw await errorFor(response)
  return (await response.json()) as IntakeForm
}

/** Record a completed intake form. */
export async function submitIntake(
  sessionToken: string,
  body: SubmitIntakeRequest,
): Promise<IntakeSubmission> {
  let response: Response
  try {
    response = await fetch(buildApiUrl(SUBMISSIONS_PATH), {
      method: "POST",
      headers: { ...authHeaders(sessionToken), "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
  } catch {
    throw unavailable()
  }
  if (!response.ok) throw await errorFor(response)
  return (await response.json()) as IntakeSubmission
}
