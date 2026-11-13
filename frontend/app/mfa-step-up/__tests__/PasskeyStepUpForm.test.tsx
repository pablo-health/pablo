// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The step-up page upgrades a session that is already signed in, so its
 * verify call sends that session's token: the API uses it to refuse a passkey
 * that belongs to another account.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

const { beginAuthentication, finishAuthentication, startAuthentication, signInWithCustomToken } =
  vi.hoisted(() => ({
    beginAuthentication: vi.fn(),
    finishAuthentication: vi.fn(),
    startAuthentication: vi.fn(),
    signInWithCustomToken: vi.fn(),
  }))

vi.mock("@/lib/api/passkey", () => ({ beginAuthentication, finishAuthentication }))
vi.mock("@simplewebauthn/browser", () => ({
  startAuthentication,
  WebAuthnError: class extends Error {},
}))
vi.mock("firebase/auth", () => ({ signInWithCustomToken }))
vi.mock("@/lib/firebase", () => ({ getFirebaseAuth: () => undefined }))
vi.mock("@/lib/auth/signOutAndClear", () => ({ signOutAndClear: vi.fn() }))
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock("@tanstack/react-query", () => ({ useQueryClient: () => ({ clear: vi.fn() }) }))

import { PasskeyStepUpForm } from "../PasskeyStepUpForm"

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("PasskeyStepUpForm", () => {
  it("verifies the assertion as a step-up", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })))
    beginAuthentication.mockResolvedValue({ challenge: "c" })
    startAuthentication.mockResolvedValue({ id: "assertion" })
    finishAuthentication.mockResolvedValue({ custom_token: "stepped-up" })
    signInWithCustomToken.mockResolvedValue({
      user: { getIdToken: vi.fn().mockResolvedValue("id"), refreshToken: "r" },
    })

    render(<PasskeyStepUpForm />)
    fireEvent.click(screen.getByRole("button", { name: "Use passkey" }))

    await waitFor(() =>
      expect(finishAuthentication).toHaveBeenCalledWith({ id: "assertion" }, "step-up"),
    )
  })
})
