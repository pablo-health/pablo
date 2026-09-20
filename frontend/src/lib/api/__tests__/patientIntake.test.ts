// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The intake client: where it goes, what it carries, and how it classifies a
 * refusal.
 *
 * The last test reads the module's own source. The rule it enforces — this
 * client never reaches for a clinician's Firebase session — is invisible to
 * types and to any behavioural test that mocks `fetch`, and importing one
 * helper from `@/lib/api/client` would silently attach the wrong principal's
 * token to a patient's request.
 */

import { readFileSync } from "node:fs"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  fetchIntakeForm,
  PatientIntakeError,
  submitIntake,
  type SubmitIntakeRequest,
} from "../patientIntake"

const TOKEN = "portal-session-token"
const API = "http://localhost:8000"

const FORM = {
  identity: { first_name: "Dana", last_name: "Okonkwo", date_of_birth: "1988-04-02" },
  reason_prompt: "What brings you in?",
  instruments: [],
}

const BODY: SubmitIntakeRequest = {
  name_confirmed: true,
  dob_confirmed: true,
  corrections: null,
  reason_text: "Sleep has been bad",
  phq9: { "1": 0 },
  gad7: { "1": 0 },
}

function jsonResponse(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

function stubFetch(response: Response | Error) {
  const fn = vi.fn(async (_url: string, _init: RequestInit): Promise<Response> => {
    if (response instanceof Error) throw response
    return response
  })
  vi.stubGlobal("fetch", fn)
  return fn
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("fetchIntakeForm", () => {
  it("GETs the form route with the session token as a bearer", async () => {
    const fetchMock = stubFetch(jsonResponse(200, FORM))

    await expect(fetchIntakeForm(TOKEN)).resolves.toEqual(FORM)

    expect(fetchMock).toHaveBeenCalledWith(`${API}/api/patient/intake/form`, {
      method: "GET",
      headers: { Authorization: `Bearer ${TOKEN}`, Accept: "application/json" },
    })
  })

  it.each([
    [401, { error: { code: "UNAUTHORIZED" } }],
    [403, { error: { code: "STEP_UP_REQUIRED" } }],
    // The same envelope, nested the way FastAPI's HTTPException renders it.
    [403, { detail: { error: { code: "STEP_UP_REQUIRED" } } }],
  ])("reads %i as an expired link", async (status, body) => {
    stubFetch(jsonResponse(status, body))

    await expect(fetchIntakeForm(TOKEN)).rejects.toMatchObject({ kind: "expired" })
  })

  it("keeps a 403 that is not a step-up separate from an expired link", async () => {
    stubFetch(jsonResponse(403, { error: { code: "FORBIDDEN" } }))

    await expect(fetchIntakeForm(TOKEN)).rejects.toMatchObject({ kind: "unavailable" })
  })

  it("reads a network failure as the server not answering", async () => {
    stubFetch(new TypeError("Failed to fetch"))

    const error = await fetchIntakeForm(TOKEN).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(PatientIntakeError)
    expect((error as PatientIntakeError).kind).toBe("unavailable")
  })
})

describe("submitIntake", () => {
  it("POSTs the submissions route with the bearer and the body verbatim", async () => {
    const fetchMock = stubFetch(jsonResponse(201, { id: "s1", submitted_at: "x", measures: [] }))

    await submitIntake(TOKEN, BODY)

    expect(fetchMock).toHaveBeenCalledWith(`${API}/api/patient/intake/submissions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${TOKEN}`,
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(BODY),
    })
    expect(fetchMock.mock.calls[0][1].body).not.toContain("patient_id")
  })

  it.each([
    [429, "rate_limited"],
    [400, "rejected"],
    [500, "unavailable"],
  ])("reads %i as %s", async (status, kind) => {
    stubFetch(jsonResponse(status, {}))

    await expect(submitIntake(TOKEN, BODY)).rejects.toMatchObject({ kind })
  })
})

describe("the module's imports", () => {
  it("borrows nothing from the clinician auth stack", () => {
    // Vitest runs from the frontend root. Only the import statements are
    // examined: the prose above them names Firebase on purpose, to say why
    // none of it is imported.
    const source = readFileSync("src/lib/api/patientIntake.ts", "utf8")
    const imports = source.match(/^import .*$/gm) ?? []

    expect(imports).toEqual(['import { buildApiUrl } from "@/lib/api/client"'])
    for (const forbidden of [/auth-context/, /useAuthQuery/, /firebase/i, /getIdToken/]) {
      expect(imports.join("\n")).not.toMatch(forbidden)
    }
    // Nothing reaches around the import list either.
    expect(source).not.toMatch(/getIdToken|getClientAuthProvider/)
  })
})
