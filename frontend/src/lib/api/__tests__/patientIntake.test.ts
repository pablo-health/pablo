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
  fetchAssignment,
  fetchIntakeForm,
  listAssignments,
  PatientIntakeError,
  saveAnswer,
  submitAssignment,
} from "../patientIntake"

const TOKEN = "portal-session-token"
const API = "http://localhost:8000"

const FORM = {
  identity: { first_name: "Dana", last_name: "Okonkwo", date_of_birth: "1988-04-02" },
  reason_prompt: "What brings you in?",
  instruments: [],
}

const ASSIGNMENT_ID = "6f2a0e1c-77d4-4f9a-9c2b-1a3e5d7f9b01"
const ITEM_ID = "1b9c4d2e-5f60-4a81-9e33-7c0d2a4b6e88"

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

describe("the assignment routes", () => {
  it("lists the caller's own forms with no patient id anywhere", async () => {
    const fetchMock = stubFetch(jsonResponse(200, []))

    await expect(listAssignments(TOKEN)).resolves.toEqual([])

    expect(fetchMock).toHaveBeenCalledWith(`${API}/api/patient/intake/assignments`, {
      method: "GET",
      headers: { Authorization: `Bearer ${TOKEN}`, Accept: "application/json" },
    })
  })

  it("reads one form by id", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { id: ASSIGNMENT_ID, items: [] }))

    await fetchAssignment(TOKEN, ASSIGNMENT_ID)

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${API}/api/patient/intake/assignments/${ASSIGNMENT_ID}`,
    )
  })

  it("PUTs one answer under its own item, wrapped in `value`", async () => {
    const fetchMock = stubFetch(
      jsonResponse(200, {
        item_id: ITEM_ID,
        saved_at: "2026-09-20T10:00:00Z",
        status: "in_progress",
        progress: { complete: false, missing: [] },
      }),
    )

    await saveAnswer(TOKEN, ASSIGNMENT_ID, ITEM_ID, { text: "Sleep has been bad" })

    expect(fetchMock).toHaveBeenCalledWith(
      `${API}/api/patient/intake/assignments/${ASSIGNMENT_ID}/items/${ITEM_ID}`,
      {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${TOKEN}`,
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ value: { text: "Sleep has been bad" } }),
      },
    )
    // The route reads the patient off the session; there is no field for one.
    expect(fetchMock.mock.calls[0][1].body).not.toContain("patient_id")
  })

  it("POSTs a submit with no body at all", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { receipt_code: "ABCDEFGH" }))

    await submitAssignment(TOKEN, ASSIGNMENT_ID)

    expect(fetchMock).toHaveBeenCalledWith(
      `${API}/api/patient/intake/assignments/${ASSIGNMENT_ID}/submit`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${TOKEN}`, Accept: "application/json" },
      },
    )
  })

  it.each([
    [429, "rate_limited"],
    [400, "rejected"],
    [409, "closed"],
    [422, "invalid"],
    [500, "unavailable"],
  ])("reads %i as %s", async (status, kind) => {
    stubFetch(jsonResponse(status, {}))

    await expect(submitAssignment(TOKEN, ASSIGNMENT_ID)).rejects.toMatchObject({ kind })
  })

  it("carries the server's own sentence for a refused answer", async () => {
    stubFetch(
      jsonResponse(422, {
        error: {
          code: "UNPROCESSABLE_ENTITY",
          message: "What brings you in is still blank.",
          details: { item_id: ITEM_ID },
        },
      }),
    )

    const error = await saveAnswer(TOKEN, ASSIGNMENT_ID, ITEM_ID, {}).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(PatientIntakeError)
    expect((error as PatientIntakeError).serverMessage).toBe("What brings you in is still blank.")
  })

  it("carries what is still outstanding when a submit is refused", async () => {
    stubFetch(
      jsonResponse(422, {
        error: {
          code: "UNPROCESSABLE_ENTITY",
          message: "Some questions still need an answer.",
          details: { missing: [ITEM_ID] },
        },
      }),
    )

    const error = await submitAssignment(TOKEN, ASSIGNMENT_ID).catch((e: unknown) => e)

    expect((error as PatientIntakeError).missing).toEqual([ITEM_ID])
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
