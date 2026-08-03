// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { useState } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"
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
} = vi.hoisted(() => ({
  signInWithEmailAndPassword: vi.fn(),
  signInWithCustomToken: vi.fn(),
  startAuthentication: vi.fn(),
  browserSupportsWebAuthn: vi.fn(),
  beginAuthentication: vi.fn(),
  finishAuthentication: vi.fn(),
  useConfig: vi.fn(),
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
  useConfig.mockReturnValue({ passkeysEnabled: true })
  browserSupportsWebAuthn.mockReturnValue(true)
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
