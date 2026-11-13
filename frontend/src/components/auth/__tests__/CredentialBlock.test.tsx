// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { useState } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { UserCredential } from "firebase/auth"

// CredentialBlock is the shared credential-acquisition seam under /login and
// /native-auth. These tests pin the seam's contract: passkey affordances are
// config- and capability-gated, a resolved credential reaches the host exactly
// once with the right method tag, and hosts can turn sign-up off.

const {
  signInWithEmailAndPassword,
  signInWithCustomToken,
  startAuthentication,
  browserSupportsWebAuthn,
  beginAuthentication,
  finishAuthentication,
  useConfig,
  getUserStatus,
  cancelCeremony,
  reportFrontendError,
  clearFirebaseAuthStorage,
} = vi.hoisted(() => ({
  signInWithEmailAndPassword: vi.fn(),
  signInWithCustomToken: vi.fn(),
  startAuthentication: vi.fn(),
  browserSupportsWebAuthn: vi.fn(),
  beginAuthentication: vi.fn(),
  finishAuthentication: vi.fn(),
  useConfig: vi.fn(),
  getUserStatus: vi.fn(),
  cancelCeremony: vi.fn(),
  reportFrontendError: vi.fn(),
  clearFirebaseAuthStorage: vi.fn(),
}))

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
vi.mock("@/lib/firebase", () => ({ getFirebaseAuth: vi.fn() }))
vi.mock("@/lib/config", () => ({ useConfig }))
vi.mock("@/lib/api/passkey", () => ({ beginAuthentication, finishAuthentication }))
vi.mock("@/lib/api/users", () => ({ getUserStatus }))
vi.mock("@simplewebauthn/browser", () => ({
  startAuthentication,
  browserSupportsWebAuthn,
  WebAuthnError: class WebAuthnError extends Error {},
  WebAuthnAbortService: { cancelCeremony },
}))
vi.mock("@/lib/firebaseAuthRecovery", () => ({ clearFirebaseAuthStorage }))
vi.mock("@/lib/feErrorReporter", () => ({ reportFrontendError }))

import { WebAuthnError } from "@simplewebauthn/browser"
import { CredentialBlock, type CredentialBlockProps } from "../CredentialBlock"

const PASSKEY_BUTTON = "Sign in with a passkey"
const RECOVERY_LINK = "Lost your passkey? Use a recovery code"

function renderBlock(props: Partial<CredentialBlockProps> = {}) {
  const onCredential = vi.fn().mockResolvedValue(undefined)

  function Host() {
    const [email, setEmail] = useState("")
    return (
      <CredentialBlock
        onCredential={onCredential}
        email={email}
        onEmailChange={setEmail}
        renderShell={(form) => <div>{form}</div>}
        {...props}
      />
    )
  }

  const utils = render(<Host />)
  return { onCredential, ...utils }
}

beforeEach(() => {
  vi.clearAllMocks()
  useConfig.mockReturnValue({ passkeysEnabled: true, googleSignInEnabled: true })
  browserSupportsWebAuthn.mockReturnValue(true)
  // Keep step-up inactive unless a test opts into it.
  getUserStatus.mockResolvedValue({ session_mfa_satisfied: true, has_passkey: false })
})

/** A first-factor credential: signed in, but carrying no second factor. */
function firstFactorCredential(uid = "u1") {
  return {
    user: { uid, getIdToken: vi.fn().mockResolvedValue("id-token"), refreshToken: "r" },
  } as unknown as UserCredential
}

async function signInWithPassword(container: HTMLElement) {
  fireEvent.change(container.querySelector("#email")!, {
    target: { value: "clinician@example.com" },
  })
  fireEvent.change(container.querySelector("#password")!, {
    target: { value: "correct horse battery" },
  })
  fireEvent.submit(container.querySelector("form")!)
}

describe("CredentialBlock passkey step-up", () => {
  it("asks for the passkey instead of resolving a first-factor credential", async () => {
    signInWithEmailAndPassword.mockResolvedValue(firstFactorCredential())
    getUserStatus.mockResolvedValue({ session_mfa_satisfied: false, has_passkey: true })

    const { onCredential, container } = renderBlock()
    await signInWithPassword(container)

    expect(await screen.findByRole("button", { name: "Use passkey" })).not.toBeNull()
    expect(onCredential).not.toHaveBeenCalled()
  })

  it("hands the host the UPGRADED credential once the passkey is asserted", async () => {
    const upgraded = { user: { uid: "u1", getIdToken: vi.fn() } } as unknown as UserCredential
    signInWithEmailAndPassword.mockResolvedValue(firstFactorCredential())
    getUserStatus.mockResolvedValue({ session_mfa_satisfied: false, has_passkey: true })
    beginAuthentication.mockResolvedValue({ challenge: "c" })
    startAuthentication.mockResolvedValue({ id: "assertion" })
    finishAuthentication.mockResolvedValue({ custom_token: "stepped-up" })
    signInWithCustomToken.mockResolvedValue(upgraded)

    const { onCredential, container } = renderBlock()
    await signInWithPassword(container)
    fireEvent.click(await screen.findByRole("button", { name: "Use passkey" }))

    await waitFor(() => expect(onCredential).toHaveBeenCalledTimes(1))
    expect(onCredential).toHaveBeenCalledWith(upgraded, "email")
  })

  it("does not interrupt a session that already cleared a factor", async () => {
    const credential = firstFactorCredential()
    signInWithEmailAndPassword.mockResolvedValue(credential)
    getUserStatus.mockResolvedValue({ session_mfa_satisfied: true, has_passkey: true })

    const { onCredential, container } = renderBlock()
    await signInWithPassword(container)

    await waitFor(() => expect(onCredential).toHaveBeenCalledWith(credential, "email"))
    expect(screen.queryByRole("button", { name: "Use passkey" })).toBeNull()
  })

  it("falls through when the status read fails, leaving the gate to decide", async () => {
    const credential = firstFactorCredential()
    signInWithEmailAndPassword.mockResolvedValue(credential)
    getUserStatus.mockRejectedValue(new Error("network"))

    const { onCredential, container } = renderBlock()
    await signInWithPassword(container)

    await waitFor(() => expect(onCredential).toHaveBeenCalledWith(credential, "email"))
  })
})

describe("CredentialBlock passkey gating", () => {
  it("offers the passkey button and recovery-code link when enabled and supported", async () => {
    renderBlock()

    expect(
      await screen.findByRole("button", { name: PASSKEY_BUTTON })
    ).not.toBeNull()
    expect(screen.getByText(RECOVERY_LINK)).not.toBeNull()
  })

  it("offers neither when passkeys are disabled in config", async () => {
    useConfig.mockReturnValue({ passkeysEnabled: false })

    renderBlock()

    // The gate resolves in a post-mount effect; give it a tick to settle.
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: PASSKEY_BUTTON })).toBeNull()
    })
    expect(screen.queryByText(RECOVERY_LINK)).toBeNull()
    expect(browserSupportsWebAuthn).not.toHaveBeenCalled()
  })
})

describe("CredentialBlock Google gating", () => {
  it("offers Google sign-in when the deployment has a Google client", async () => {
    renderBlock()

    expect(await screen.findByRole("button", { name: /Continue with Google/ })).not.toBeNull()
  })

  it("offers only email and password when it has none", async () => {
    useConfig.mockReturnValue({ passkeysEnabled: false, googleSignInEnabled: false })

    renderBlock()

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /Continue with Google/ })).toBeNull()
    })
    expect(screen.getByLabelText("Email")).not.toBeNull()
  })
})

describe("CredentialBlock credential resolution", () => {
  it("calls onCredential exactly once with method 'email' on password sign-in", async () => {
    const credential = { user: { uid: "u1" } } as unknown as UserCredential
    signInWithEmailAndPassword.mockResolvedValue(credential)

    const { onCredential, container } = renderBlock()

    fireEvent.change(container.querySelector("#email")!, {
      target: { value: "clinician@example.com" },
    })
    fireEvent.change(container.querySelector("#password")!, {
      target: { value: "correct horse battery" },
    })
    fireEvent.submit(container.querySelector("form")!)

    await waitFor(() => expect(onCredential).toHaveBeenCalledTimes(1))
    expect(onCredential).toHaveBeenCalledWith(credential, "email")
    expect(signInWithEmailAndPassword).toHaveBeenCalledWith(
      undefined,
      "clinician@example.com",
      "correct horse battery",
    )
  })

  it("calls onCredential exactly once with method 'passkey' after the ceremony", async () => {
    const credential = { user: { uid: "u1" } } as unknown as UserCredential
    beginAuthentication.mockResolvedValue({ challenge: "c" })
    startAuthentication.mockResolvedValue({ id: "assertion" })
    finishAuthentication.mockResolvedValue({ custom_token: "custom-token" })
    signInWithCustomToken.mockResolvedValue(credential)

    const { onCredential } = renderBlock()

    fireEvent.click(await screen.findByRole("button", { name: PASSKEY_BUTTON }))

    await waitFor(() => expect(onCredential).toHaveBeenCalledTimes(1))
    expect(onCredential).toHaveBeenCalledWith(credential, "passkey")
    expect(startAuthentication).toHaveBeenCalledWith({
      optionsJSON: { challenge: "c" },
    })
    expect(signInWithCustomToken).toHaveBeenCalledWith(undefined, "custom-token")
  })
})

describe("CredentialBlock email verification on sign-in", () => {
  function passwordCredential(emailVerified: boolean) {
    return {
      user: {
        uid: "u1",
        email: "clinician@example.com",
        emailVerified,
        getIdToken: vi.fn().mockResolvedValue("id-token"),
      },
    } as unknown as UserCredential
  }

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("shows the verify screen and opens no session for an unverified email", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch")
    signInWithEmailAndPassword.mockResolvedValue(passwordCredential(false))

    const { onCredential, container } = renderBlock()
    await signInWithPassword(container)

    expect(await screen.findByText("Check Your Email")).not.toBeNull()
    expect(screen.getByRole("button", { name: "Resend Verification Email" })).not.toBeNull()
    expect(onCredential).not.toHaveBeenCalled()
    expect(getUserStatus).not.toHaveBeenCalled()
    expect(fetchSpy).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole("button", { name: "Back to Sign In" }))
    expect(await screen.findByRole("button", { name: "Sign In" })).not.toBeNull()
  })

  it("signs a verified email straight in", async () => {
    const credential = passwordCredential(true)
    signInWithEmailAndPassword.mockResolvedValue(credential)

    const { onCredential, container } = renderBlock()
    await signInWithPassword(container)

    await waitFor(() => expect(onCredential).toHaveBeenCalledWith(credential, "email"))
    expect(screen.queryByText("Check Your Email")).toBeNull()
  })
})

describe("CredentialBlock sign-up gating", () => {
  it("hides the 'Create account' toggle when allowSignUp is false", async () => {
    renderBlock({ allowSignUp: false })

    await screen.findByRole("button", { name: PASSKEY_BUTTON })
    expect(screen.queryByText("Create account")).toBeNull()
  })

  it("shows the 'Create account' toggle by default", async () => {
    renderBlock()

    expect(await screen.findByText("Create account")).not.toBeNull()
  })
})

describe("CredentialBlock host notice", () => {
  function NoticeHost({ notice }: { notice?: string }) {
    const [email, setEmail] = useState("")
    return (
      <CredentialBlock
        onCredential={vi.fn()}
        email={email}
        onEmailChange={setEmail}
        notice={notice}
        renderShell={(form) => <div>{form}</div>}
      />
    )
  }

  it("shows a notice until the next attempt, and each new notice after that", async () => {
    signInWithEmailAndPassword.mockReturnValue(new Promise(() => {}))
    const { container, rerender } = render(<NoticeHost notice="You were signed out." />)

    expect(screen.getByText("You were signed out.")).toBeInTheDocument()

    await signInWithPassword(container)
    await waitFor(() => expect(screen.queryByText("You were signed out.")).toBeNull())

    rerender(<NoticeHost notice="You were signed out." />)
    expect(screen.queryByText("You were signed out.")).toBeNull()

    rerender(<NoticeHost notice="Failed to get authentication tokens." />)
    expect(screen.getByText("Failed to get authentication tokens.")).toBeInTheDocument()
  })
})

describe("CredentialBlock last-used hint", () => {
  beforeEach(() => {
    localStorage.setItem("pablo:lastAuthMethod", "passkey")
  })

  afterEach(() => {
    localStorage.removeItem("pablo:lastAuthMethod")
  })

  it("flags the method this device signed in with last", async () => {
    renderBlock({ showLastUsed: true })

    const passkey = await screen.findByRole("button", { name: PASSKEY_BUTTON })
    expect(passkey.parentElement).toHaveTextContent("Last used")
    expect(screen.getAllByText("Last used")).toHaveLength(1)
  })

  it("shows no hint when the host does not ask for one", async () => {
    renderBlock()

    await screen.findByRole("button", { name: PASSKEY_BUTTON })
    expect(screen.queryByText("Last used")).toBeNull()
  })
})

describe("CredentialBlock passkey step time limits", () => {
  // A promise that never settles: the step the browser got stuck on.
  const hang = () => new Promise<never>(() => {})
  const STUCK = "Sign-in got stuck."
  const RESET = "Reset and try again"
  const credential = { user: { uid: "u1" } } as unknown as UserCredential

  let warn: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    beginAuthentication.mockResolvedValue({ challenge: "c", timeout: 60_000 })
    startAuthentication.mockResolvedValue({ id: "assertion" })
    finishAuthentication.mockResolvedValue({ custom_token: "custom-token" })
    signInWithCustomToken.mockResolvedValue(credential)
    reportFrontendError.mockResolvedValue(undefined)
    warn = vi.spyOn(console, "warn").mockImplementation(() => {})
  })

  afterEach(() => {
    vi.useRealTimers()
    warn.mockRestore()
  })

  // Find the button on real timers, then click and let the clock run on fake
  // ones, so the step's limit is the only thing that can end the wait.
  async function clickAndWait(button: string, justShortMs: number) {
    const target = await screen.findByRole("button", { name: button })
    vi.useFakeTimers()
    fireEvent.click(target)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(justShortMs)
    })
    expect(screen.queryByText(STUCK)).toBeNull()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    vi.useRealTimers()
  }

  function expectReported(step: string) {
    expect(reportFrontendError).toHaveBeenCalledTimes(1)
    const report = reportFrontendError.mock.calls[0][0] as Error
    expect(report.name).toBe(`PasskeyStepTimeout:${step}`)
    expect(report.stack).toContain(`step=${step}`)
    expect(report.stack).not.toMatch(/clinician|@/)
  }

  const cases: { step: string; limitMs: number; stall: () => void }[] = [
    { step: "challenge", limitMs: 15_000, stall: () => beginAuthentication.mockImplementation(hang) },
    // The ceremony's own 60 s timeout plus 15 s of grace: room for a person.
    { step: "webauthn", limitMs: 75_000, stall: () => startAuthentication.mockImplementation(hang) },
    { step: "verify", limitMs: 15_000, stall: () => finishAuthentication.mockImplementation(hang) },
    { step: "sign-in", limitMs: 20_000, stall: () => signInWithCustomToken.mockImplementation(hang) },
  ]

  for (const { step, limitMs, stall } of cases) {
    it(`stops the spinner and offers the reset when the ${step} step never finishes`, async () => {
      stall()
      const { onCredential } = renderBlock()

      await clickAndWait(PASSKEY_BUTTON, limitMs - 1)

      expect(await screen.findByText(STUCK)).toBeInTheDocument()
      expect(screen.getByRole("button", { name: RESET })).toBeInTheDocument()
      expect(screen.getByRole("button", { name: PASSKEY_BUTTON })).not.toBeDisabled()
      expect(onCredential).not.toHaveBeenCalled()
      expectReported(step)
    })
  }

  it("treats a host that never finishes as the sign-in step stalling", async () => {
    const onCredential = vi.fn(hang)
    renderBlock({ onCredential })

    await clickAndWait(PASSKEY_BUTTON, 20_000 - 1)

    expect(await screen.findByText(STUCK)).toBeInTheDocument()
    expect(onCredential).toHaveBeenCalledTimes(1)
    expectReported("sign-in")
  })

  it("gives the passkey prompt the default ceremony time when the options carry none", async () => {
    beginAuthentication.mockResolvedValue({ challenge: "c" })
    startAuthentication.mockImplementation(hang)
    renderBlock()

    await clickAndWait(PASSKEY_BUTTON, 75_000 - 1)

    expect(await screen.findByText(STUCK)).toBeInTheDocument()
  })

  it("cancels the passkey request the browser never ended", async () => {
    startAuthentication.mockImplementation(hang)
    renderBlock()

    await clickAndWait(PASSKEY_BUTTON, 75_000 - 1)

    await screen.findByText(STUCK)
    expect(cancelCeremony).toHaveBeenCalledTimes(1)
  })

  it("does not cancel anything when another step stalls", async () => {
    finishAuthentication.mockImplementation(hang)
    renderBlock()

    await clickAndWait(PASSKEY_BUTTON, 15_000 - 1)

    await screen.findByText(STUCK)
    expect(cancelCeremony).not.toHaveBeenCalled()
  })

  it("the reset clears the stored sign-in state and reloads", async () => {
    signInWithCustomToken.mockImplementation(hang)
    clearFirebaseAuthStorage.mockResolvedValue(undefined)
    const realLocation = window.location
    const reload = vi.fn()
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: { ...realLocation, reload },
    })
    try {
      renderBlock()
      await clickAndWait(PASSKEY_BUTTON, 20_000 - 1)

      fireEvent.click(await screen.findByRole("button", { name: RESET }))

      await waitFor(() => expect(reload).toHaveBeenCalledTimes(1))
      expect(clearFirebaseAuthStorage).toHaveBeenCalledTimes(1)
    } finally {
      Object.defineProperty(window, "location", {
        configurable: true,
        writable: true,
        value: realLocation,
      })
    }
  })

  it("a sign-in that finishes in time shows no stall and reports nothing", async () => {
    const { onCredential } = renderBlock()

    fireEvent.click(await screen.findByRole("button", { name: PASSKEY_BUTTON }))

    await waitFor(() => expect(onCredential).toHaveBeenCalledWith(credential, "passkey"))
    expect(screen.queryByText(STUCK)).toBeNull()
    expect(reportFrontendError).not.toHaveBeenCalled()
  })

  it("a dismissed passkey prompt leaves the form alone and cancels nothing", async () => {
    const dismissed = new WebAuthnError({
      message: "dismissed",
      code: "ERROR_PASSTHROUGH_SEE_CAUSE_PROPERTY",
      cause: new Error(),
    })
    dismissed.name = "NotAllowedError"
    startAuthentication.mockRejectedValue(dismissed)
    renderBlock()

    fireEvent.click(await screen.findByRole("button", { name: PASSKEY_BUTTON }))

    await waitFor(() =>
      expect(screen.getByRole("button", { name: PASSKEY_BUTTON })).not.toBeDisabled(),
    )
    expect(startAuthentication).toHaveBeenCalledTimes(1)
    expect(cancelCeremony).not.toHaveBeenCalled()
    expect(screen.queryByText(/Passkey sign-in failed/)).toBeNull()
    expect(screen.queryByText(STUCK)).toBeNull()
  })

  it("an ordinary failure keeps its own message and offers no reset", async () => {
    finishAuthentication.mockRejectedValue(new Error("400"))
    renderBlock()

    fireEvent.click(await screen.findByRole("button", { name: PASSKEY_BUTTON }))

    expect(
      await screen.findByText("Passkey sign-in failed. Try again, or use your email and password."),
    ).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: RESET })).toBeNull()
    expect(reportFrontendError).not.toHaveBeenCalled()
  })

  for (const { step, limitMs, stall } of cases) {
    it(`step-up: offers the reset when the ${step} step never finishes`, async () => {
      signInWithEmailAndPassword.mockResolvedValue(firstFactorCredential())
      getUserStatus.mockResolvedValue({ session_mfa_satisfied: false, has_passkey: true })
      stall()
      const { onCredential, container } = renderBlock()
      await signInWithPassword(container)

      await clickAndWait("Use passkey", limitMs - 1)

      expect(await screen.findByText(STUCK)).toBeInTheDocument()
      expect(screen.getByRole("button", { name: RESET })).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "Use passkey" })).not.toBeDisabled()
      expect(onCredential).not.toHaveBeenCalled()
      expectReported(step)
    })
  }
})
