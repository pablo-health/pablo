// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
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

/**
 * The gate asks whether THIS session passed the second factor, not whether one
 * was ever enrolled. A passkey holder whose session has not used it carries an
 * enrollment date, so a gate that reads enrollment history lets them straight
 * into the dashboard.
 *
 * The status comes from the server-side fetch the layout makes before it
 * renders anything, so this is where the branch can be driven: a browser-level
 * route mock never reaches that fetch. The backend's own refusal is proven
 * where MFA is enforced.
 */
describe("DashboardLayout for a passkey holder", () => {
  // Enrolled, passkey on record: every onboarding gate passes, so only the
  // second-factor check can redirect.
  const passkeyHolder = {
    status: "approved",
    mfa_enrolled_at: "2026-01-05T10:00:00Z",
    has_passkey: true,
    is_platform_admin: false,
    is_practice_admin: false,
    name: "Dr. Example",
    email: "a@example.com",
    security_guide_version: null,
    security_guide_acknowledged_at: null,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubEnv("REQUIRE_MFA", "true")
    getServerSession.mockResolvedValue({
      token: "t",
      claims: { email: "a@example.com", emailVerified: true },
    })
  })

  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it("sends a session that has not used the passkey to step-up before anything renders", async () => {
    getCachedUserStatus.mockResolvedValue({ ...passkeyHolder, session_mfa_satisfied: false })

    // The layout throws the redirect instead of returning a tree, so none of
    // the dashboard (children, sidebar, header) is ever handed back to render.
    await expect(DashboardLayout({ children: null })).rejects.toThrow(
      "NEXT_REDIRECT /mfa-step-up",
    )
    expect(redirect).toHaveBeenCalledTimes(1)
  })

  it("lets the same holder in once the session has passed the passkey", async () => {
    getCachedUserStatus.mockResolvedValue({ ...passkeyHolder, session_mfa_satisfied: true })

    const layout = await DashboardLayout({ children: null })

    expect(redirect).not.toHaveBeenCalled()
    expect(layout).toBeTruthy()
  })
})
