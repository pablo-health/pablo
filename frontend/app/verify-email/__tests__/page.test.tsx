// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { beforeEach, describe, expect, it, vi } from "vitest"
import type { ServerSession } from "@/lib/auth/types"

const getServerSession = vi.hoisted(() => vi.fn<() => Promise<ServerSession | null>>())
const redirect = vi.hoisted(() =>
  vi.fn((path: string) => {
    throw new Error(`NEXT_REDIRECT ${path}`)
  }),
)

vi.mock("next/navigation", () => ({ redirect }))
vi.mock("@/lib/auth/server", () => ({ getServerSession }))
vi.mock("@/lib/devMode", () => ({ IS_DEV_MODE: false }))
vi.mock("../VerifyEmailForm", () => ({ VerifyEmailForm: () => null }))

import VerifyEmailPage from "../page"

function session(claims: ServerSession["claims"]): ServerSession {
  return { token: "t", claims }
}

describe("VerifyEmailPage", () => {
  beforeEach(() => {
    getServerSession.mockReset()
    redirect.mockClear()
  })

  it("sends a signed-out visitor to sign in", async () => {
    getServerSession.mockResolvedValue(null)
    await expect(VerifyEmailPage()).rejects.toThrow("NEXT_REDIRECT /login")
  })

  it("sends a verified session on to the dashboard", async () => {
    getServerSession.mockResolvedValue(session({ email: "a@example.com", emailVerified: true }))
    await expect(VerifyEmailPage()).rejects.toThrow("NEXT_REDIRECT /dashboard")
  })

  it("renders for a session whose email is unverified", async () => {
    getServerSession.mockResolvedValue(session({ email: "a@example.com", emailVerified: false }))
    await expect(VerifyEmailPage()).resolves.toBeTruthy()
    expect(redirect).not.toHaveBeenCalled()
  })
})
