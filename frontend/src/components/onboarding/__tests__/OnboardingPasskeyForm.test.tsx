// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { renderWithProviders } from "@/test/renderWithProviders"

const push = vi.fn()
const replace = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace }),
}))

const browserSupportsWebAuthn = vi.fn()
const startRegistration = vi.fn()
vi.mock("@simplewebauthn/browser", () => ({
  startRegistration: (...args: unknown[]) => startRegistration(...args),
  browserSupportsWebAuthn: () => browserSupportsWebAuthn(),
  WebAuthnError: class WebAuthnError extends Error {
    name = "WebAuthnError"
  },
}))

const beginRegistration = vi.fn()
const finishRegistration = vi.fn()
vi.mock("@/lib/api/passkey", () => ({
  beginRegistration: (...args: unknown[]) => beginRegistration(...args),
  finishRegistration: (...args: unknown[]) => finishRegistration(...args),
}))

const trackOnboardingStepCompleted = vi.fn()
vi.mock("@/lib/analytics/onboarding", () => ({
  trackOnboardingStepCompleted: (...args: unknown[]) => trackOnboardingStepCompleted(...args),
}))

vi.mock("firebase/auth", () => ({
  signInWithCustomToken: vi.fn(),
}))

vi.mock("@/lib/firebase", () => ({
  getFirebaseAuth: () => ({}),
}))

import { OnboardingPasskeyForm } from "../OnboardingPasskeyForm"

describe("OnboardingPasskeyForm", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("falls back to TOTP when WebAuthn is unavailable and does not mark enrolment complete", async () => {
    browserSupportsWebAuthn.mockReturnValue(false)
    const user = userEvent.setup()

    renderWithProviders(<OnboardingPasskeyForm />)

    expect(
      screen.getByText(/can.t create a passkey.*use an authenticator app instead/i),
    ).toBeInTheDocument()

    const createButton = screen.getByRole("button", { name: /create a passkey/i })
    expect(createButton).toBeDisabled()

    await user.click(screen.getByRole("button", { name: /use an authenticator app instead/i }))

    expect(push).toHaveBeenCalledWith("/onboarding/mfa")
    expect(beginRegistration).not.toHaveBeenCalled()
    expect(replace).not.toHaveBeenCalled()
  })

  it("hides the authenticator-app fallback link when the surface has no TOTP step", () => {
    browserSupportsWebAuthn.mockReturnValue(false)

    renderWithProviders(<OnboardingPasskeyForm showTotpFallback={false} />)

    expect(
      screen.queryByRole("button", { name: /use an authenticator app instead/i }),
    ).not.toBeInTheDocument()
  })

  it("creates a passkey and completes enrolment when WebAuthn is supported", async () => {
    browserSupportsWebAuthn.mockReturnValue(true)
    beginRegistration.mockResolvedValue({ challenge: "abc" })
    startRegistration.mockResolvedValue({ id: "cred-1" })
    finishRegistration.mockResolvedValue({ custom_token: null, backup_codes: [] })
    const user = userEvent.setup()

    renderWithProviders(<OnboardingPasskeyForm />)

    const createButton = screen.getByRole("button", { name: /create a passkey/i })
    expect(createButton).toBeEnabled()

    await user.click(createButton)

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/onboarding"))
    expect(trackOnboardingStepCompleted).toHaveBeenCalledWith("passkey")
  })
})
