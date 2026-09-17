// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { renderWithProviders } from "@/test/renderWithProviders"

const push = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => new URLSearchParams(),
}))

const currentUser = {
  email: "clinician@example.com",
  providerData: [{ providerId: "password" }],
  getIdToken: vi.fn().mockResolvedValue("id-token"),
}

vi.mock("@/lib/firebase", () => ({
  getFirebaseAuth: () => ({ currentUser }),
}))

vi.mock("@/lib/auth/firebase/client", () => ({
  useFirebaseUser: () => currentUser,
}))

const post = vi.fn().mockResolvedValue({})
vi.mock("@/lib/api/client", () => ({
  post: (...args: unknown[]) => post(...args),
}))

vi.mock("qrcode.react", () => ({
  QRCodeSVG: () => <div data-testid="qr-code" />,
}))

const totpSecret = {
  secretKey: "SECRETKEY",
  generateQrCodeUrl: vi.fn(() => "otpauth://totp/Pablo:clinician@example.com"),
}

const generateSecret = vi.fn().mockResolvedValue(totpSecret)
const getSession = vi.fn().mockResolvedValue("mfa-session")
const assertionForEnrollment = vi.fn((_secret: unknown, code: string) => ({ code }))
// A real backend only rejects the assertion at enroll() time — the code is
// opaque until then, which is exactly the "blocks continue" behavior under test.
const enroll = vi.fn((assertion: { code: string }) => {
  if (assertion.code !== "123456") {
    const err = Object.assign(new Error("invalid"), {
      code: "auth/invalid-verification-code",
    })
    return Promise.reject(err)
  }
  return Promise.resolve()
})

vi.mock("firebase/auth", () => ({
  TotpMultiFactorGenerator: {
    generateSecret: (...args: unknown[]) => generateSecret(...args),
    assertionForEnrollment: (...args: unknown[]) => assertionForEnrollment(...args as [unknown, string]),
  },
  TotpSecret: class {},
  multiFactor: () => ({ enrolledFactors: [], getSession, enroll }),
  reauthenticateWithPopup: vi.fn(),
  reauthenticateWithCredential: vi.fn(),
  EmailAuthProvider: { credential: vi.fn() },
  GoogleAuthProvider: class {},
  sendEmailVerification: vi.fn(),
}))

import { FirebaseMfaEnrollmentForm } from "../MfaEnrollmentForm"

describe("FirebaseMfaEnrollmentForm", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    currentUser.getIdToken.mockResolvedValue("id-token")
    generateSecret.mockResolvedValue(totpSecret)
    getSession.mockResolvedValue("mfa-session")
    post.mockResolvedValue({})
  })

  it("blocks continue until a 6-digit code is entered", async () => {
    const user = userEvent.setup()
    renderWithProviders(<FirebaseMfaEnrollmentForm />)

    const codeInput = await screen.findByLabelText("Verification Code")
    const submit = screen.getByRole("button", { name: /enable mfa/i })
    expect(submit).toBeDisabled()

    await user.type(codeInput, "123")
    expect(submit).toBeDisabled()

    await user.type(codeInput, "456")
    expect(submit).toBeEnabled()
  })

  it("does not enroll or redirect when the code fails verification", async () => {
    const user = userEvent.setup()
    renderWithProviders(<FirebaseMfaEnrollmentForm />)

    const codeInput = await screen.findByLabelText("Verification Code")
    await user.type(codeInput, "000000")
    await user.click(screen.getByRole("button", { name: /enable mfa/i }))

    await screen.findByText(/invalid verification code/i)
    expect(post).not.toHaveBeenCalled()
    expect(push).not.toHaveBeenCalled()
  })

  it("enrolls and redirects once the code verifies", async () => {
    const user = userEvent.setup()
    renderWithProviders(<FirebaseMfaEnrollmentForm />)

    const codeInput = await screen.findByLabelText("Verification Code")
    await user.type(codeInput, "123456")
    await user.click(screen.getByRole("button", { name: /enable mfa/i }))

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/users/me/mfa-enrolled", {}, "id-token"),
    )
    expect(push).toHaveBeenCalledWith("/dashboard")
  })
})
