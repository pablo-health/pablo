// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

// @vitest-environment node
// The route handler runs in the Node.js runtime and reads container env.

import { NextRequest } from "next/server"
import { afterEach, describe, expect, it, vi } from "vitest"

import { GET } from "../app/api/config/route"

// Off a practice's own portal host, the route falls back to configured env.
vi.mock("@/lib/portal-host/practice-host-api", () => ({
  practiceHostApiOrigin: vi.fn(async () => null),
}))

async function servedSupportEmail(): Promise<unknown> {
  const response = await GET(new NextRequest("http://localhost:3000/api/config"))
  const body = (await response.json()) as Record<string, unknown>
  return body.supportEmail
}

describe("/api/config supportEmail", () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it("serves the address the deployment set in SUPPORT_EMAIL", async () => {
    vi.stubEnv("SUPPORT_EMAIL", " help@clinic.example ")
    expect(await servedSupportEmail()).toBe("help@clinic.example")
  })

  // An empty string is what the client reads as "show no contact line".
  it("serves an empty string when SUPPORT_EMAIL is unset", async () => {
    vi.stubEnv("SUPPORT_EMAIL", undefined)
    expect(await servedSupportEmail()).toBe("")
  })
})
