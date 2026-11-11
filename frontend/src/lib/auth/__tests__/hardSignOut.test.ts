// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The provider split in hardSignOut. The Header's own tests cover the
 * cookie-session provider end to end; this pins the OIDC branch, whose
 * sign-out navigates to the identity provider itself and must not have that
 * navigation replaced by one to the logout path.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"

const { provider } = vi.hoisted(() => ({
  provider: { id: "firebase", signOut: vi.fn() },
}))

vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => provider,
}))

import { hardSignOut } from "../hardSignOut"

let assignSpy: ReturnType<typeof vi.fn>

beforeEach(() => {
  provider.signOut.mockReset().mockResolvedValue(undefined)
  assignSpy = vi.fn()
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: { assign: assignSpy, href: "http://test/" },
  })
})

describe("hardSignOut", () => {
  it("leaves the navigation to the OIDC provider's own sign-out", async () => {
    provider.id = "oidc"

    await hardSignOut()

    expect(provider.signOut).toHaveBeenCalledTimes(1)
    expect(assignSpy).not.toHaveBeenCalled()
  })

  it("sends the cookie-session provider through the logout path", async () => {
    provider.id = "firebase"

    await hardSignOut()

    expect(provider.signOut).toHaveBeenCalledWith({ wipePersisted: true })
    expect(assignSpy).toHaveBeenCalledWith("/api/logout")
  })
})
