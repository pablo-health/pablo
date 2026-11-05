// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { UserCredential } from "firebase/auth"

// The desktop app's sign-in handoff. A clinician whose second factor is a
// passkey signs in with it (or with a recovery code when the passkey is
// gone) and lands on the same one-time-code redirect back to the app as a
// password or Google sign-in does. These tests drive the real CredentialBlock
// under the native host so the whole path is exercised, not just the seam.

const {
  signInWithEmailAndPassword,
  signInWithCustomToken,
  startAuthentication,
  browserSupportsWebAuthn,
  beginAuthentication,
  finishAuthentication,
  redeemRecoveryCode,
  useConfig,
  getUserStatus,
} = vi.hoisted(() => ({
  signInWithEmailAndPassword: vi.fn(),
  signInWithCustomToken: vi.fn(),
  startAuthentication: vi.fn(),
  browserSupportsWebAuthn: vi.fn(),
  beginAuthentication: vi.fn(),
  finishAuthentication: vi.fn(),
  redeemRecoveryCode: vi.fn(),
  useConfig: vi.fn(),
  getUserStatus: vi.fn(),
}))

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(window.location.search),
}))
vi.mock("next/image", () => ({ default: () => null }))
vi.mock("firebase/auth", () => ({
  signInWithPopup: vi.fn(),
  signInWithRedirect: vi.fn(),
  signInWithEmailAndPassword,
  signInWithCustomToken,
  sendPasswordResetEmail: vi.fn(),
  createUserWithEmailAndPassword: vi.fn(),
  sendEmailVerification: vi.fn(),
  GoogleAuthProvider: vi.fn(),
  getMultiFactorResolver: vi.fn(),
}))
vi.mock("@/lib/firebase", () => ({ getFirebaseAuth: vi.fn(() => ({ signOut: vi.fn() })) }))
vi.mock("@/lib/config", () => ({ useConfig }))
vi.mock("@/lib/api/passkey", () => ({
  beginAuthentication,
  finishAuthentication,
  redeemRecoveryCode,
}))
vi.mock("@/lib/api/users", () => ({ getUserStatus }))
vi.mock("@simplewebauthn/browser", () => ({
  startAuthentication,
  browserSupportsWebAuthn,
  WebAuthnError: class WebAuthnError extends Error {},
}))
vi.mock("@/lib/firebaseAuthRecovery", () => ({ clearFirebaseAuthStorage: vi.fn() }))

import { FirebaseNativeAuthScreen } from "../NativeAuthScreen"

const API_URL = "https://api.example.test"
const REDIRECT_URI = "pablohealth://auth/callback"
const STATE = "opaque-state"
const PASSKEY_BUTTON = "Sign in with a passkey"
const RECOVERY_LINK = "Lost your passkey? Use a recovery code"

const fetchMock = vi.fn()

function codeResponse(code: string) {
  return { ok: true, status: 200, json: async () => ({ code }) }
}

function mfaRequiredResponse() {
  return {
    ok: false,
    status: 403,
    json: async () => ({ detail: { error: { code: "MFA_REQUIRED" } } }),
  }
}

/** A credential as Firebase hands it back after a custom-token sign-in. */
function credential(idToken: string) {
  return {
    user: {
      uid: "u1",
      email: "clinician@example.com",
      emailVerified: true,
      getIdToken: vi.fn().mockResolvedValue(idToken),
      refreshToken: `refresh-${idToken}`,
    },
  } as unknown as UserCredential
}

function nativeCodeCall() {
  const call = fetchMock.mock.calls.find(([url]) => url === `${API_URL}/api/auth/native/code`)
  expect(call).toBeDefined()
  return JSON.parse(call![1].body as string)
}

function fillPassword(container: HTMLElement, ids: { email: string; password: string }) {
  fireEvent.change(container.querySelector(`#${ids.email}`)!, {
    target: { value: "clinician@example.com" },
  })
  fireEvent.change(container.querySelector(`#${ids.password}`)!, {
    target: { value: "correct horse battery" },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  // The page reads its query through useSearchParams and leaves by assigning
  // window.location.href, so the stub carries the query in and records the
  // destination out.
  Object.defineProperty(window, "location", {
    value: {
      href: "",
      search: `?redirect_uri=${encodeURIComponent(REDIRECT_URI)}&state=${STATE}`,
      origin: "https://app.example.test",
    },
    writable: true,
    configurable: true,
  })
  vi.stubGlobal("fetch", fetchMock)
  useConfig.mockReturnValue({ apiUrl: API_URL, passkeysEnabled: true, googleSignInEnabled: true })
  browserSupportsWebAuthn.mockReturnValue(true)
  getUserStatus.mockResolvedValue({ session_mfa_satisfied: true, has_passkey: false })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("native-auth passkey gating", () => {
  it("offers the passkey button when the flag is on and the browser supports WebAuthn", async () => {
    render(<FirebaseNativeAuthScreen />)

    expect(await screen.findByRole("button", { name: PASSKEY_BUTTON })).not.toBeNull()
    expect(screen.getByText(RECOVERY_LINK)).not.toBeNull()
  })

  it("offers nothing passkey-shaped when the flag is off", async () => {
    useConfig.mockReturnValue({ apiUrl: API_URL, passkeysEnabled: false, googleSignInEnabled: true })

    render(<FirebaseNativeAuthScreen />)

    expect(await screen.findByLabelText("Email")).not.toBeNull()
    expect(screen.queryByRole("button", { name: PASSKEY_BUTTON })).toBeNull()
    expect(screen.queryByText(RECOVERY_LINK)).toBeNull()
    expect(browserSupportsWebAuthn).not.toHaveBeenCalled()
  })

  it("offers nothing passkey-shaped when the browser cannot run the ceremony", async () => {
    browserSupportsWebAuthn.mockReturnValue(false)

    render(<FirebaseNativeAuthScreen />)

    expect(await screen.findByLabelText("Email")).not.toBeNull()
    expect(screen.queryByRole("button", { name: PASSKEY_BUTTON })).toBeNull()
    expect(screen.queryByText(RECOVERY_LINK)).toBeNull()
  })
})

describe("native-auth passkey sign-in", () => {
  it("ends at the app's one-time-code redirect", async () => {
    beginAuthentication.mockResolvedValue({ challenge: "c" })
    startAuthentication.mockResolvedValue({ id: "assertion" })
    finishAuthentication.mockResolvedValue({ custom_token: "passkey-token" })
    signInWithCustomToken.mockResolvedValue(credential("id-after-passkey"))
    fetchMock.mockResolvedValue(codeResponse("one-time-code"))

    render(<FirebaseNativeAuthScreen />)
    fireEvent.click(await screen.findByRole("button", { name: PASSKEY_BUTTON }))

    await waitFor(() =>
      expect(window.location.href).toBe(`${REDIRECT_URI}?code=one-time-code&state=${STATE}`),
    )
    expect(nativeCodeCall()).toEqual({
      id_token: "id-after-passkey",
      refresh_token: "refresh-id-after-passkey",
      redirect_uri: REDIRECT_URI,
    })
    // The passkey IS the second factor: no status read, no step-up detour.
    expect(getUserStatus).not.toHaveBeenCalled()
  })

  it("reaches the same redirect through a recovery code", async () => {
    signInWithEmailAndPassword.mockResolvedValue(credential("first-factor"))
    redeemRecoveryCode.mockResolvedValue({ custom_token: "recovery-token" })
    signInWithCustomToken.mockResolvedValue(credential("id-after-recovery"))
    fetchMock.mockResolvedValue(codeResponse("one-time-code"))

    const { container } = render(<FirebaseNativeAuthScreen />)
    fireEvent.click(await screen.findByText(RECOVERY_LINK))

    fireEvent.change(await screen.findByLabelText("Recovery code"), {
      target: { value: "ABCDE-FGHJK" },
    })
    fillPassword(container, { email: "recovery-email", password: "recovery-password" })
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }))

    await waitFor(() =>
      expect(window.location.href).toBe(`${REDIRECT_URI}?code=one-time-code&state=${STATE}`),
    )
    expect(redeemRecoveryCode).toHaveBeenCalledWith("ABCDE-FGHJK")
    expect(signInWithCustomToken).toHaveBeenCalledWith(expect.anything(), "recovery-token")
    expect(nativeCodeCall().id_token).toBe("id-after-recovery")
  })
})

describe("native-auth second-factor refusal", () => {
  async function signInWithPasswordOnly(container: HTMLElement) {
    fillPassword(container, { email: "email", password: "password" })
    fireEvent.submit(container.querySelector("form")!)
  }

  it("keeps a passkey account on the page instead of sending it to app enrollment", async () => {
    signInWithEmailAndPassword.mockResolvedValue(credential("first-factor"))
    // The block's own status read fails, so the first-factor credential falls
    // through to the exchange, which refuses it; the retry says the account
    // holds a passkey.
    getUserStatus
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValue({ session_mfa_satisfied: false, has_passkey: true })
    fetchMock.mockResolvedValue(mfaRequiredResponse())

    const { container } = render(<FirebaseNativeAuthScreen />)
    await screen.findByRole("button", { name: PASSKEY_BUTTON })
    await signInWithPasswordOnly(container)

    expect(
      await screen.findByText("Your account has a passkey. Use it to finish signing in."),
    ).not.toBeNull()
    expect(screen.getByRole("button", { name: PASSKEY_BUTTON })).not.toBeNull()
    expect(window.location.href).toBe("")
  })

  it("sends an account with no second factor to enrollment", async () => {
    signInWithEmailAndPassword.mockResolvedValue(credential("first-factor"))
    getUserStatus.mockResolvedValue({ session_mfa_satisfied: false, has_passkey: false })
    fetchMock.mockResolvedValue(mfaRequiredResponse())

    const { container } = render(<FirebaseNativeAuthScreen />)
    await screen.findByLabelText("Email")
    await signInWithPasswordOnly(container)

    await waitFor(() => expect(window.location.href).toMatch(/^\/mfa-enrollment\?returnTo=/))
    const returnTo = new URL(window.location.href, "https://app.example.test").searchParams.get(
      "returnTo",
    )
    expect(returnTo).toBe(`/native-auth?redirect_uri=${encodeURIComponent(REDIRECT_URI)}&state=${STATE}`)
  })
})
