// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { useEffect, type ReactNode } from "react"
import { render } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { testRuntimeConfig } from "@/test/runtimeConfig"

// Stands in for the SDK holder with its one rule intact: asking for the auth
// instance before anyone initialized it throws, which is what the API client
// reads as "no token" and why a page's first requests went out without one.
const firebase = vi.hoisted(() => {
  let auth: object | undefined
  return {
    reset: () => {
      auth = undefined
    },
    initFirebase: () => {
      auth ??= {}
      return auth
    },
    getFirebaseAuth: () => {
      if (!auth) throw new Error("Firebase not initialized.")
      return auth
    },
  }
})

vi.mock("@/lib/firebase", () => ({
  initFirebase: firebase.initFirebase,
  getFirebaseAuth: firebase.getFirebaseAuth,
}))
vi.mock("firebase/auth", () => ({
  onIdTokenChanged: vi.fn(() => () => {}),
  onAuthStateChanged: vi.fn(() => () => {}),
  signOut: vi.fn(),
}))
vi.mock("@/lib/config", () => ({ useConfig: () => testRuntimeConfig() }))

import { getFirebaseAuth } from "@/lib/firebase"
import { useFirebaseAuthState } from "../client"

function AuthHost({ children }: { children: ReactNode }) {
  useFirebaseAuthState()
  return <>{children}</>
}

describe("useFirebaseAuthState", () => {
  beforeEach(() => firebase.reset())

  // React runs a child's effects before its parent's. On a full page load the
  // dashboard's first requests — the idle controller's session check, every
  // query — start from effects below the auth provider, so Firebase has to
  // exist before those run, not after.
  it("initializes Firebase before the page's own effects ask for a token", () => {
    let seen: "initialized" | "missing" | undefined
    function FirstRequest() {
      useEffect(() => {
        try {
          getFirebaseAuth()
          seen = "initialized"
        } catch {
          seen = "missing"
        }
      }, [])
      return null
    }

    render(
      <AuthHost>
        <FirstRequest />
      </AuthHost>,
    )

    expect(seen).toBe("initialized")
  })
})
