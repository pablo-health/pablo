// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient refills client.
 *
 * Pins the same things the messaging client's spec does: every request
 * carries the patient session token, no request carries a patient id, a
 * failure never quotes the payload it failed on, and the module does not
 * reach into the clinician auth stack.
 */

import { readFileSync } from "node:fs"
import { join } from "node:path"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  PatientRefillsError,
  createRefillRequest,
  listRefillMedications,
  listRefillRequests,
} from "../patientRefills"

vi.mock("@/lib/api/client", () => ({
  buildApiUrl: (endpoint: string) => `http://test${endpoint}`,
}))

const TOKEN = "session-token"

function okResponse(body: unknown, status = 200): Response {
  // A partial Response: the client reads only ok, status and json().
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal("fetch", fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function lastCall(): [string, RequestInit] {
  const call = fetchMock.mock.calls.at(-1)
  return [call![0] as string, (call![1] ?? {}) as RequestInit]
}

function authHeader(init: RequestInit): string | undefined {
  return (init.headers as Record<string, string> | undefined)?.Authorization
}

describe("patientRefills client", () => {
  it("lists medications and requests on the patient routes", async () => {
    fetchMock.mockResolvedValue(okResponse({ data: [], total: 0 }))

    await listRefillMedications(TOKEN)
    expect(lastCall()[0]).toBe("http://test/api/patient/refills/medications")

    await listRefillRequests(TOKEN)
    expect(lastCall()[0]).toBe("http://test/api/patient/refills")
  })

  it("posts a listed medication by id", async () => {
    fetchMock.mockResolvedValue(okResponse({ id: "r1" }, 201))

    await createRefillRequest(TOKEN, { medication_id: "med-1", pharmacy_text: "Main St" })

    const [url, init] = lastCall()
    expect(url).toBe("http://test/api/patient/refills")
    expect(init.method).toBe("POST")
    expect(JSON.parse(init.body as string)).toEqual({
      medication_id: "med-1",
      medication_text: null,
      pharmacy_text: "Main St",
      patient_note: null,
    })
  })

  it("posts a typed medication name", async () => {
    fetchMock.mockResolvedValue(okResponse({ id: "r1" }, 201))

    await createRefillRequest(TOKEN, { medication_text: "Lamotrigine", patient_note: "Low" })

    expect(JSON.parse(lastCall()[1].body as string)).toEqual({
      medication_id: null,
      medication_text: "Lamotrigine",
      pharmacy_text: null,
      patient_note: "Low",
    })
  })

  it("sends the token on every request and a patient id on none", async () => {
    fetchMock.mockResolvedValue(okResponse({ data: [], total: 0 }))

    await listRefillMedications(TOKEN)
    await listRefillRequests(TOKEN)
    await createRefillRequest(TOKEN, { medication_text: "X" })

    for (const [url, init] of fetchMock.mock.calls) {
      expect(authHeader(init as RequestInit)).toBe(`Bearer ${TOKEN}`)
      expect(url as string).not.toContain("patient_id")
    }
  })

  it("reports a failure by status and never by payload", async () => {
    fetchMock.mockResolvedValue(okResponse({ detail: "Sertraline" }, 422))

    const error = await createRefillRequest(TOKEN, {
      medication_text: "Sertraline",
      patient_note: "A thing I asked",
    }).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(PatientRefillsError)
    const refillsError = error as PatientRefillsError
    expect(refillsError.status).toBe(422)
    const text = `${refillsError.message}${refillsError.stack ?? ""}`
    expect(text).not.toContain("A thing I asked")
    expect(text).not.toContain("Sertraline")
  })

  it("does not import the clinician auth stack", () => {
    const source = readFileSync(join(__dirname, "..", "patientRefills.ts"), "utf8")

    expect(source).not.toMatch(/from "@\/lib\/auth/)
    expect(source).not.toMatch(/getAuthHeader/)
    expect(source).not.toMatch(/getClientAuthProvider/)
    expect(source).not.toMatch(/apiClient/)
  })
})
