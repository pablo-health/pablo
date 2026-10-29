// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { useState } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
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
} = vi.hoisted(() => ({
  signInWithEmailAndPassword: vi.fn(),
  signInWithCustomToken: vi.fn(),
  startAuthentication: vi.fn(),
  browserSupportsWebAuthn: vi.fn(),
  beginAuthentication: vi.fn(),
  finishAuthentication: vi.fn(),
  useConfig: vi.fn(),
  getUserStatus: vi.fn(),
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
}))
vi.mock("@/lib/firebaseAuthRecovery", () => ({ clearFirebaseAuthStorage: vi.fn() }))

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
