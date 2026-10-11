// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { testRuntimeConfig } from "@/test/runtimeConfig"

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

const runtimeConfig = testRuntimeConfig({ firebaseApiKey: "runtime-key" })

vi.mock("@/lib/config-provider", () => ({
  useConfig: () => runtimeConfig,
}))

const applyActionCode = vi.fn()

vi.mock("firebase/auth", () => ({
  applyActionCode: (...args: unknown[]) => applyActionCode(...args),
  checkActionCode: vi.fn(),
  confirmPasswordReset: vi.fn(),
  verifyPasswordResetCode: vi.fn(),
}))

import { FirebaseAuthActionScreen } from "../AuthActionScreen"

function fakeAuth(currentUser: { reload: () => Promise<void> } | null) {
  return { authStateReady: vi.fn().mockResolvedValue(undefined), currentUser }
}

describe("FirebaseAuthActionScreen", () => {
  const replace = vi.fn()

  beforeEach(() => {
    searchParams.forEach((_v, k) => searchParams.delete(k))
    searchParams.set("mode", "verifyEmail")
    searchParams.set("oobCode", "code123")
    initFirebase.mockReset()
    applyActionCode.mockReset().mockResolvedValue(undefined)
    getFirebaseAuth.mockReset().mockReturnValue(fakeAuth(null))
    replace.mockReset()
    vi.stubGlobal("location", { ...window.location, replace, origin: "https://app.test" })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  // The action page is reached straight from an emailed link, so every query
  // parameter on it is attacker-writable. initFirebase must take its key from
  // the runtime config — a build-time NEXT_PUBLIC_* value is empty on any
  // deployment that sets it only on the running container.
  it("initializes Firebase from the runtime config, ignoring an apiKey on the URL", async () => {
    searchParams.set("apiKey", "evil")

    render(<FirebaseAuthActionScreen />)

    await waitFor(() => expect(initFirebase).toHaveBeenCalled())
    expect(initFirebase).toHaveBeenCalledWith(
      expect.objectContaining({ apiKey: "runtime-key", projectId: "test-project" }),
    )
  })

  it("spends the action code only once", async () => {
    const { rerender } = render(<FirebaseAuthActionScreen />)
    await screen.findByText("Your email is verified.")
    rerender(<FirebaseAuthActionScreen />)

    expect(applyActionCode).toHaveBeenCalledTimes(1)
  })

  it("offers a way into Pablo when the verifier is not signed in", async () => {
    render(<FirebaseAuthActionScreen />)

    await screen.findByText("Your email is verified.")
    expect(screen.getByRole("link", { name: "Continue to Pablo" })).toHaveAttribute(
      "href",
      "/login",
    )
    expect(replace).not.toHaveBeenCalled()
  })

  it("sends a signed-in verifier straight into the app", async () => {
    const reload = vi.fn().mockResolvedValue(undefined)
    getFirebaseAuth.mockReturnValue(fakeAuth({ reload }))
    searchParams.set("continueUrl", "https://app.test/login")

    render(<FirebaseAuthActionScreen />)

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"))
    expect(reload).toHaveBeenCalled()
  })

  it("returns a signed-in verifier to where the email asked, such as MFA enrollment", async () => {
    getFirebaseAuth.mockReturnValue(fakeAuth({ reload: vi.fn().mockResolvedValue(undefined) }))
    searchParams.set("continueUrl", "https://app.test/mfa-enrollment")

    render(<FirebaseAuthActionScreen />)

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/mfa-enrollment"))
  })
})
