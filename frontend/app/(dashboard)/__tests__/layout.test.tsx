// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { beforeEach, describe, expect, it, vi } from "vitest"
import type { ServerSession } from "@/lib/auth/types"
import { ApiError } from "@/lib/api/client"

const getServerSession = vi.hoisted(() => vi.fn<() => Promise<ServerSession | null>>())
const getCachedUserStatus = vi.hoisted(() => vi.fn())
const redirect = vi.hoisted(() =>
  vi.fn((path: string) => {
    throw Object.assign(new Error(`NEXT_REDIRECT ${path}`), { digest: `NEXT_REDIRECT;${path}` })
  }),
)

vi.mock("next/navigation", () => ({ redirect }))
vi.mock("@/lib/auth/server", () => ({ getServerSession }))
vi.mock("@/lib/api/users.server", () => ({ getCachedUserStatus }))
vi.mock("@/lib/devMode", () => ({ IS_DEV_MODE: false }))
vi.mock("@/components/layout/Sidebar", () => ({ Sidebar: () => null }))
vi.mock("@/components/layout/Header", () => ({ Header: () => null }))
vi.mock("@/components/DashboardErrorBoundary", () => ({ DashboardErrorBoundary: () => null }))
vi.mock("@/components/IdleTimeout", () => ({ IdleTimeout: () => null }))
vi.mock("@/components/theme/ThemeSync", () => ({ ThemeSync: () => null }))
vi.mock("@/components/sessions/DraftNotices", () => ({ DraftNotices: () => null }))
vi.mock("@/components/layout/UpdateNotice", () => ({ UpdateNotice: () => null }))
vi.mock("@/hooks/usePeopleTerm", () => ({ PeopleTermProvider: () => null }))

import DashboardLayout from "../layout"

function refusedWith(code: string, status: number) {
  getCachedUserStatus.mockRejectedValue(new ApiError(code, "refused", {}, status))
}

describe("DashboardLayout when the API refuses the session", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // No emailVerified claim: a cookie from before the claim was carried.
    getServerSession.mockResolvedValue({ token: "t", claims: { email: "a@example.com" } })
  })

  it("sends an unverified email to verify it, not to a sign-in that bounces back", async () => {
    refusedWith("EMAIL_NOT_VERIFIED", 403)
    await expect(DashboardLayout({ children: null })).rejects.toThrow(
      "NEXT_REDIRECT /verify-email",
    )
  })

  it("still sends any other refusal to sign in", async () => {
    refusedWith("FORBIDDEN", 403)
    await expect(DashboardLayout({ children: null })).rejects.toThrow("NEXT_REDIRECT /login")
  })
})
