// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, waitFor } from "@testing-library/react"

const searchParams = new URLSearchParams()

vi.mock("next/navigation", () => ({
  useSearchParams: () => searchParams,
}))

const initFirebase = vi.fn()
const getFirebaseAuth = vi.fn()

vi.mock("@/lib/firebase", () => ({
  initFirebase: (...args: unknown[]) => initFirebase(...args),
  getFirebaseAuth: () => getFirebaseAuth(),
}))

vi.mock("firebase/auth", () => ({
  applyActionCode: vi.fn().mockResolvedValue(undefined),
  checkActionCode: vi.fn(),
  confirmPasswordReset: vi.fn(),
  verifyPasswordResetCode: vi.fn(),
}))

import { FirebaseAuthActionScreen } from "../AuthActionScreen"

// The action page is reached straight from an emailed link, so every query
// parameter on it is attacker-writable. initFirebase must always take its
// key from the build's own env, never from the URL.
describe("FirebaseAuthActionScreen", () => {
  const originalApiKey = process.env.NEXT_PUBLIC_FIREBASE_API_KEY

  beforeEach(() => {
    searchParams.forEach((_v, k) => searchParams.delete(k))
    searchParams.set("mode", "verifyEmail")
    searchParams.set("oobCode", "code123")
    initFirebase.mockReset()
    getFirebaseAuth.mockReset().mockReturnValue({})
    process.env.NEXT_PUBLIC_FIREBASE_API_KEY = "build-time-key"
  })

  afterEach(() => {
    process.env.NEXT_PUBLIC_FIREBASE_API_KEY = originalApiKey
  })

  it("initializes Firebase with the build-time key, ignoring an apiKey on the URL", async () => {
    searchParams.set("apiKey", "evil")

    render(<FirebaseAuthActionScreen />)

    await waitFor(() => expect(initFirebase).toHaveBeenCalled())
    expect(initFirebase).toHaveBeenCalledWith(
      expect.objectContaining({ apiKey: "build-time-key" }),
    )
  })
})
