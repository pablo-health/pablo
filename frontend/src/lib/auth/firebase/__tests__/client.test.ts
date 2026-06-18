// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Auth, User } from "firebase/auth"

// The boot auth-state sync must never wedge the loading splash: a restored
// session with an expired refresh token rejects getIdToken(), and the cookie
// sync is a network call that can stall. withTimeout bounds both, and
// clearStaleSession is the recovery the SDK's own stuck-state handler can't
// reach. These pin the two load-bearing pieces.

const { signOut, clearFirebaseAuthStorage } = vi.hoisted(() => ({
  signOut: vi.fn(),
  clearFirebaseAuthStorage: vi.fn(),
}))

vi.mock("firebase/auth", () => ({
  signOut,
  onAuthStateChanged: vi.fn(),
  onIdTokenChanged: vi.fn(),
}))
vi.mock("@/lib/firebaseAuthRecovery", () => ({ clearFirebaseAuthStorage }))
vi.mock("@/lib/firebase", () => ({ getFirebaseAuth: vi.fn(), initFirebase: vi.fn() }))
vi.mock("@/lib/config", () => ({ useConfig: vi.fn() }))

import { clearStaleSession, syncAuthTick, withTimeout } from "../client"

describe("withTimeout", () => {
  it("resolves with the value when the promise settles in time", async () => {
    await expect(withTimeout(Promise.resolve("ok"), 1000, "x")).resolves.toBe("ok")
  })

  it("rejects with a labeled error once the deadline passes", async () => {
    vi.useFakeTimers()
    try {
      const pending = new Promise<string>(() => {})
      const raced = withTimeout(pending, 50, "getIdToken")
      const assertion = expect(raced).rejects.toThrow(/getIdToken timed out after 50ms/)
      await vi.advanceTimersByTimeAsync(50)
      await assertion
    } finally {
      vi.useRealTimers()
    }
  })

  it("clears the timer when the promise resolves first", async () => {
    const clearSpy = vi.spyOn(globalThis, "clearTimeout")
    await withTimeout(Promise.resolve(1), 1000, "x")
    expect(clearSpy).toHaveBeenCalled()
    clearSpy.mockRestore()
  })
})

describe("clearStaleSession", () => {
  const auth = {} as Auth

  beforeEach(() => {
    signOut.mockReset().mockResolvedValue(undefined)
    clearFirebaseAuthStorage.mockReset().mockResolvedValue(undefined)
    global.fetch = vi.fn().mockResolvedValue({ ok: true } as Response)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("signs out, wipes stored auth, and clears the server cookie", async () => {
    await clearStaleSession(auth)
    expect(signOut).toHaveBeenCalledWith(auth)
    expect(clearFirebaseAuthStorage).toHaveBeenCalledOnce()
    expect(global.fetch).toHaveBeenCalledWith("/api/logout")
  })

  it("still wipes storage when signOut throws (wedged SDK)", async () => {
    signOut.mockRejectedValue(new Error("wedged"))
    await expect(clearStaleSession(auth)).resolves.toBeUndefined()
    expect(clearFirebaseAuthStorage).toHaveBeenCalledOnce()
  })

  it("swallows a failing cookie clear", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network"))
    await expect(clearStaleSession(auth)).resolves.toBeUndefined()
    expect(clearFirebaseAuthStorage).toHaveBeenCalledOnce()
  })
})

describe("syncAuthTick", () => {
  const auth = {} as Auth
  const setUser = vi.fn()

  const fakeUser = (getIdToken: () => Promise<string>) =>
    ({ uid: "u1", email: "u@x", displayName: null, photoURL: null, getIdToken }) as unknown as User

  beforeEach(() => {
    setUser.mockReset()
    signOut.mockReset().mockResolvedValue(undefined)
    clearFirebaseAuthStorage.mockReset().mockResolvedValue(undefined)
    global.fetch = vi.fn().mockResolvedValue({ ok: true } as Response)
    // Both failure branches log at warn, not error, so the smoke
    // console-error guard stays green. Silence to keep test output clean.
    vi.spyOn(console, "warn").mockImplementation(() => {})
    vi.spyOn(console, "error").mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("syncs the cookie and surfaces the user on the happy path", async () => {
    await syncAuthTick(auth, fakeUser(() => Promise.resolve("tok")), setUser)
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/login",
      expect.objectContaining({ headers: { Authorization: "Bearer tok" } }),
    )
    expect(setUser).toHaveBeenCalledWith(expect.objectContaining({ uid: "u1" }))
    expect(clearFirebaseAuthStorage).not.toHaveBeenCalled()
  })

  it("clears the stale session when the token refresh rejects", async () => {
    await syncAuthTick(
      auth,
      fakeUser(() => Promise.reject(new Error("auth/user-token-expired"))),
      setUser,
    )
    expect(setUser).toHaveBeenCalledWith(null)
    expect(clearFirebaseAuthStorage).toHaveBeenCalledOnce()
    expect(console.error).not.toHaveBeenCalled()
  })

  it("keeps the session when only the cookie sync fails (transient abort)", async () => {
    global.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"))
    await syncAuthTick(auth, fakeUser(() => Promise.resolve("tok")), setUser)
    // The user is still surfaced and the session is NOT torn down — this is
    // the regression that signed users out and tripped the smoke guard.
    expect(setUser).toHaveBeenCalledWith(expect.objectContaining({ uid: "u1" }))
    expect(setUser).not.toHaveBeenCalledWith(null)
    expect(clearFirebaseAuthStorage).not.toHaveBeenCalled()
    expect(console.error).not.toHaveBeenCalled()
  })

  it("clears the user (signed out) when there is no firebase user", async () => {
    await syncAuthTick(auth, null, setUser)
    expect(setUser).toHaveBeenCalledWith(null)
    expect(global.fetch).toHaveBeenCalledWith("/api/logout")
    expect(clearFirebaseAuthStorage).not.toHaveBeenCalled()
  })
})
