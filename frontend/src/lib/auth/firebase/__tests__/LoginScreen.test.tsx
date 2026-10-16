// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render } from "@testing-library/react"

// A forced logout arrives on /login as a fresh page load. AuthProvider
// initializes Firebase in its own effect, and React runs this screen's
// effects first — so the stale-session cleanup must wait for auth loading
// to finish, or getFirebaseAuth() throws and the error boundary replaces
// the login screen.

const { authState, getFirebaseAuth, clearStaleSession } = vi.hoisted(() => ({
  authState: { user: null, loading: true },
  getFirebaseAuth: vi.fn(),
  clearStaleSession: vi.fn(),
}))

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }))
vi.mock("next/image", () => ({ default: () => null }))
vi.mock("@/lib/firebase", () => ({ getFirebaseAuth }))
vi.mock("../client", () => ({ clearStaleSession }))
vi.mock("@/lib/auth-context", () => ({ useAuth: () => authState }))
vi.mock("@/lib/firebaseAuthRecovery", () => ({
  installAuthRecovery: vi.fn(),
  consumeRecoveryNotice: () => false,
}))
vi.mock("@/components/auth", () => ({
  AuthCard: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AuthFooter: () => null,
  AuthHeader: () => null,
  CredentialBlock: ({ notice }: { notice?: string }) => <p>{notice}</p>,
}))
vi.mock("@/components/theme/ThemeSwitcher", () => ({ ThemeSwitcher: () => null }))

import { FirebaseLoginScreen } from "../LoginScreen"

const FIREBASE_AUTH = {}

describe("FirebaseLoginScreen forced-logout arrival", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/login?reason=idle_timeout")
    authState.loading = true
    getFirebaseAuth.mockReset().mockImplementation(() => {
      throw new Error("Firebase not initialized.")
    })
    clearStaleSession.mockReset().mockResolvedValue(undefined)
  })

  afterEach(() => {
    window.history.replaceState({}, "", "/")
  })

  it("waits for Firebase to initialize before clearing the stale session", () => {
    const { rerender } = render(<FirebaseLoginScreen />)
    expect(clearStaleSession).not.toHaveBeenCalled()

    getFirebaseAuth.mockReturnValue(FIREBASE_AUTH)
    authState.loading = false
    rerender(<FirebaseLoginScreen />)

    expect(clearStaleSession).toHaveBeenCalledOnce()
    expect(clearStaleSession).toHaveBeenCalledWith(FIREBASE_AUTH)
    expect(window.location.pathname + window.location.search).toBe("/login")
  })

  // The dashboard sends a sign-in whose email belongs to another account here.
  // Without clearing the session, the still-valid sign-in bounces straight
  // back to the dashboard and loops.
  it("explains an account conflict and clears the session", () => {
    window.history.replaceState({}, "", "/login?reason=account_conflict")
    getFirebaseAuth.mockReturnValue(FIREBASE_AUTH)
    authState.loading = false

    const { getByText } = render(<FirebaseLoginScreen />)

    expect(getByText(/already linked to a different sign-in/)).toBeTruthy()
    expect(clearStaleSession).toHaveBeenCalledWith(FIREBASE_AUTH)
  })
})
