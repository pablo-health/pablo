// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  _resetForTests,
  deriveRouteTemplate,
  installGlobalErrorReporter,
  reportFrontendError,
  scrubPayload,
  scrubString,
  topStackFrame,
} from "../feErrorReporter"

// THERAPY-s9r3 — the HIPAA value of these tests is proving that PHI-shaped
// inputs cannot make it onto the wire to /api/internal/fe-error. The
// scrubber is the load-bearing function; the rest verifies wiring.

describe("scrubString", () => {
  it("rewrites a single UUID to {id}", () => {
    const input = "GET /patients/550e8400-e29b-41d4-a716-446655440000/sessions"
    expect(scrubString(input, 500)).toBe("GET /patients/{id}/sessions")
  })

  it("rewrites multiple UUIDs", () => {
    const input =
      "/patients/550e8400-e29b-41d4-a716-446655440000/sessions/" +
      "11111111-2222-3333-4444-555555555555"
    expect(scrubString(input, 500)).toBe("/patients/{id}/sessions/{id}")
  })

  it("truncates with marker when over the cap", () => {
    const result = scrubString("x".repeat(2000), 200)
    expect(result.length).toBeLessThanOrEqual(200 + "...[truncated]".length)
    expect(result.endsWith("...[truncated]")).toBe(true)
  })

  it("leaves a short string alone", () => {
    expect(scrubString("hello", 200)).toBe("hello")
  })
})

describe("scrubPayload (PHI deny list)", () => {
  it("drops patient_id, patient_name, soap_text, transcript, chat_message, session_id", () => {
    const out = scrubPayload({
      error_class: "TypeError",
      stack_top_frame: "at f()",
      patient_id: "abc-123",
      patient_name: "Jane Doe",
      patient_email: "jane@example.com",
      soap_text: "Patient reports anxiety, prescribed sertraline 50mg",
      transcript: "Therapist: hi. Patient: I had a panic attack.",
      transcript_content: "...same as above...",
      note_content: "S: ...; O: ...; A: ...; P: ...",
      chat_message: "Can you call me back tonight?",
      session_id: "sess-001",
      audio_path: "gs://bucket/path.wav",
    })

    expect(out).toEqual({
      error_class: "TypeError",
      stack_top_frame: "at f()",
    })
  })

  it("drops non-string values", () => {
    const out = scrubPayload({
      error_class: "TypeError",
      // The dangerous case: a misbehaving caller stuffs a React state
      // object or array of patient ids in here. We refuse non-strings.
      reactState: { foo: "bar" } as unknown as string,
      ids: ["a", "b"] as unknown as string,
      n: 42 as unknown as string,
    })
    expect(out).toEqual({ error_class: "TypeError" })
  })

  it("keeps build_sha, user_agent, route_template, stack_top_frame, error_class", () => {
    const out = scrubPayload({
      error_class: "Error",
      stack_top_frame: "at f()",
      route_template: "/sessions/{id}",
      build_sha: "deadbeef",
      user_agent: "Mozilla/5.0",
    })
    expect(Object.keys(out).sort()).toEqual([
      "build_sha",
      "error_class",
      "route_template",
      "stack_top_frame",
      "user_agent",
    ])
  })
})

describe("topStackFrame", () => {
  it("extracts the first V8 frame line", () => {
    const stack = [
      "TypeError: Cannot read properties of undefined",
      "    at SessionEditor (/static/js/main.abc.js:1:1234)",
      "    at div (anonymous)",
    ].join("\n")
    expect(topStackFrame(stack)).toBe("at SessionEditor (/static/js/main.abc.js:1:1234)")
  })

  it("falls back to the first non-empty line for Firefox-style stacks", () => {
    const stack = "fn@file.js:1:1\nfn2@file.js:2:2"
    expect(topStackFrame(stack)).toBe("fn@file.js:1:1")
  })

  it("returns 'unknown' for a missing stack", () => {
    expect(topStackFrame(undefined)).toBe("unknown")
  })
})

describe("deriveRouteTemplate", () => {
  it("strips query strings and rewrites UUIDs", () => {
    expect(
      deriveRouteTemplate(
        "/patients/550e8400-e29b-41d4-a716-446655440000?tab=overview",
      ),
    ).toBe("/patients/{id}")
  })

  it("returns '/' for an empty path", () => {
    expect(deriveRouteTemplate("")).toBe("/")
  })
})

describe("reportFrontendError", () => {
  const origFetch = global.fetch

  beforeEach(() => {
    Object.defineProperty(global, "fetch", {
      value: vi.fn().mockResolvedValue(new Response(null, { status: 204 })),
      writable: true,
      configurable: true,
    })
  })

  afterEach(() => {
    Object.defineProperty(global, "fetch", {
      value: origFetch,
      writable: true,
      configurable: true,
    })
  })

  it("POSTs to /api/internal/fe-error with a scrubbed body", async () => {
    const err = new TypeError("boom")
    err.stack =
      "TypeError: boom\n    at fetchPatient " +
      "(/api/patients/550e8400-e29b-41d4-a716-446655440000:42:7)"
    await reportFrontendError(err, {
      routeTemplate: "/patients/550e8400-e29b-41d4-a716-446655440000",
      userAgent: "Mozilla/5.0 (Test)",
    })

    const fetchMock = global.fetch as ReturnType<typeof vi.fn>
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain("/api/internal/fe-error")
    const body = JSON.parse((init as RequestInit).body as string)

    // route_template UUID was rewritten.
    expect(body.route_template).toBe("/patients/{id}")
    // stack_top_frame is just the top frame, with the embedded UUID redacted.
    expect(body.stack_top_frame).not.toContain("550e8400")
    expect(body.stack_top_frame).toContain("{id}")
    expect(body.error_class).toBe("TypeError")
    expect(body.user_agent).toBe("Mozilla/5.0 (Test)")
  })

  it("does not throw when fetch rejects", async () => {
    Object.defineProperty(global, "fetch", {
      value: vi.fn().mockRejectedValue(new Error("network down")),
      writable: true,
      configurable: true,
    })
    const err = new Error("x")
    // The whole point of the helper is "never escalate" — if this
    // throws, an error in the boundary -> reporter chain would loop.
    await expect(
      reportFrontendError(err, { routeTemplate: "/", userAgent: "ua" }),
    ).resolves.toBeUndefined()
  })
})

describe("installGlobalErrorReporter", () => {
  beforeEach(() => {
    _resetForTests()
  })

  it("registers an error and unhandledrejection handler on window", () => {
    const addSpy = vi.spyOn(window, "addEventListener")
    installGlobalErrorReporter()
    const events = addSpy.mock.calls.map((c) => c[0])
    expect(events).toContain("error")
    expect(events).toContain("unhandledrejection")
    addSpy.mockRestore()
  })

  it("is idempotent — second call does not double-register", () => {
    const addSpy = vi.spyOn(window, "addEventListener")
    installGlobalErrorReporter()
    const after1 = addSpy.mock.calls.length
    installGlobalErrorReporter()
    expect(addSpy.mock.calls.length).toBe(after1)
    addSpy.mockRestore()
  })
})
