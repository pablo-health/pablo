// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient messaging client.
 *
 * Pins the three things that would be quietly wrong otherwise: every
 * request carries the patient session token, no request carries a
 * patient id, and a failure never quotes the payload it failed on.
 *
 * Also reads its own source to prove it does not reach into the
 * clinician auth stack. A type-level rule cannot see that; a grep can.
 */

import { readFileSync } from "node:fs"
import { join } from "node:path"
import { beforeEach, describe, expect, it, vi, afterEach } from "vitest"
import {
  PatientMessagesError,
  getMessagingSettings,
  getThread,
  listThreads,
  markThreadRead,
  sendMessage,
  startThread,
} from "../patientMessages"

vi.mock("@/lib/api/client", () => ({
  buildApiUrl: (endpoint: string) => `http://test${endpoint}`,
}))

const TOKEN = "session-token"

function okResponse(body: unknown, status = 200): Response {
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

describe("patientMessages client", () => {
  it("starts a thread on the patient route with the session token", async () => {
    fetchMock.mockResolvedValue(okResponse({ id: "t1", messages: [] }))

    await startThread(TOKEN, { subject: " Scheduling ", body: "Hello" })

    const [url, init] = lastCall()
    expect(url).toBe("http://test/api/patient/messages/threads")
    expect(init.method).toBe("POST")
    expect(authHeader(init)).toBe(`Bearer ${TOKEN}`)
    expect(JSON.parse(init.body as string)).toEqual({
      subject: "Scheduling",
      body: "Hello",
    })
  })

  it("sends a blank subject as null rather than an empty string", async () => {
    fetchMock.mockResolvedValue(okResponse({ id: "t1", messages: [] }))

    await startThread(TOKEN, { subject: "   ", body: "Hello" })

    expect(JSON.parse(lastCall()[1].body as string).subject).toBeNull()
  })

  it("sends into an existing thread", async () => {
    fetchMock.mockResolvedValue(okResponse({ id: "m1" }))

    await sendMessage(TOKEN, "t 1", "Hello")

    const [url, init] = lastCall()
    expect(url).toBe("http://test/api/patient/messages/threads/t%201/messages")
    expect(init.method).toBe("POST")
    expect(JSON.parse(init.body as string)).toEqual({ body: "Hello" })
  })

  it("lists, reads and marks read on the patient routes", async () => {
    fetchMock.mockResolvedValue(okResponse({ data: [], total: 0 }))
    await listThreads(TOKEN)
    expect(lastCall()[0]).toBe("http://test/api/patient/messages/threads")

    fetchMock.mockResolvedValue(okResponse({ id: "t1", messages: [] }))
    await getThread(TOKEN, "t1")
    expect(lastCall()[0]).toBe("http://test/api/patient/messages/threads/t1")

    fetchMock.mockResolvedValue(okResponse({ marked_read: 2 }))
    await markThreadRead(TOKEN, "t1")
    const [url, init] = lastCall()
    expect(url).toBe("http://test/api/patient/messages/threads/t1/read")
    expect(init.method).toBe("POST")
  })

  it("sends the token on every request and a patient id on none", async () => {
    fetchMock.mockResolvedValue(okResponse({ data: [], total: 0, messages: [] }))

    await listThreads(TOKEN)
    await getThread(TOKEN, "t1")
    await markThreadRead(TOKEN, "t1")
    await sendMessage(TOKEN, "t1", "Hello")
    await startThread(TOKEN, { body: "Hello" })
    await getMessagingSettings(TOKEN)

    expect(fetchMock.mock.calls).toHaveLength(6)
    for (const [url, init] of fetchMock.mock.calls) {
      expect(authHeader((init ?? {}) as RequestInit)).toBe(`Bearer ${TOKEN}`)
      expect(url as string).not.toContain("patient_id")
    }
  })

  it("resolves settings to null when the route is not served", async () => {
    fetchMock.mockResolvedValue(okResponse(null, 404))

    await expect(getMessagingSettings(TOKEN)).resolves.toBeNull()
  })

  it("still throws for a settings failure that is not a missing route", async () => {
    fetchMock.mockResolvedValue(okResponse(null, 500))

    await expect(getMessagingSettings(TOKEN)).rejects.toBeInstanceOf(
      PatientMessagesError,
    )
  })

  it("reports a failure by status and never by payload", async () => {
    fetchMock.mockResolvedValue(okResponse({ detail: "A thing I asked" }, 403))

    let error: unknown
    try {
      await sendMessage(TOKEN, "t1", "A thing I asked")
    } catch (caught) {
      error = caught
    }

    expect(error).toBeInstanceOf(PatientMessagesError)
    if (!(error instanceof PatientMessagesError)) throw new Error("unreachable")
    expect(error.status).toBe(403)
    expect(error.message).toBe("Patient messaging request failed (403)")
    expect(`${error.message}${error.stack ?? ""}`).not.toContain("A thing I asked")
  })

  it("does not import the clinician auth stack", () => {
    const source = readFileSync(
      join(process.cwd(), "src", "lib", "api", "patientMessages.ts"),
      "utf8",
    )

    expect(source).not.toMatch(/from "@\/lib\/auth/)
    expect(source).not.toMatch(/getAuthHeader/)
    expect(source).not.toMatch(/getClientAuthProvider/)
    expect(source).not.toMatch(/apiClient/)
    // The one thing it takes from the shared client is where the API lives.
    expect(source).toMatch(/import \{ buildApiUrl \} from "@\/lib\/api\/client"/)
  })
})
