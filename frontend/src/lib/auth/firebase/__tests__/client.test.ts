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

import { onAuthStateChanged } from "firebase/auth"

import { getFirebaseAuth } from "@/lib/firebase"

import {
  clearStaleSession,
  firebaseSignOut,
  getFirebaseIdToken,
  resolveCurrentUser,
  syncAuthTick,
  withTimeout,
} from "../client"

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

describe("firebaseSignOut", () => {
  beforeEach(() => {
    signOut.mockReset().mockResolvedValue(undefined)
    clearFirebaseAuthStorage.mockReset().mockResolvedValue(undefined)
    global.fetch = vi.fn().mockResolvedValue({ ok: true } as Response)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("signs out and clears the cookie, leaving persisted storage intact by default", async () => {
    await firebaseSignOut()
    expect(signOut).toHaveBeenCalledOnce()
    expect(global.fetch).toHaveBeenCalledWith("/api/logout")
    // A plain sign-out must not nuke the IndexedDB record — only the
    // idle-timeout recovery path opts into that.
    expect(clearFirebaseAuthStorage).not.toHaveBeenCalled()
  })

  it("wipes the persisted SDK session when wipePersisted is set", async () => {
    await firebaseSignOut({ wipePersisted: true })
    expect(signOut).toHaveBeenCalledOnce()
    expect(clearFirebaseAuthStorage).toHaveBeenCalledOnce()
    expect(global.fetch).toHaveBeenCalledWith("/api/logout")
  })

  it("still wipes persisted storage when the SDK sign-out throws", async () => {
    signOut.mockRejectedValue(new Error("wedged"))
    await expect(firebaseSignOut({ wipePersisted: true })).resolves.toBeUndefined()
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

// resolveCurrentUser is the guard that keeps an authenticated request from
// racing Firebase's session restore. authStateReady() resolves on the first
// listener tick, but the restored currentUser can land a beat later (post-MFA
// re-sign-in, or a hard navigation that reinits the SDK before IndexedDB is
// observed — pablo#307). Reading currentUser eagerly there yields null and the
// request goes out unauthenticated. These pin the wait so a refactor can't
// quietly reintroduce the eager read.
describe("resolveCurrentUser", () => {
  const fakeUser = () => ({ uid: "u1", email: "u@x" }) as unknown as User

  const fakeAuth = (over: Partial<Auth>): Auth =>
    ({
      authStateReady: vi.fn().mockResolvedValue(undefined),
      currentUser: null,
      ...over,
    }) as unknown as Auth

  beforeEach(() => {
    vi.mocked(onAuthStateChanged).mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("returns currentUser once auth state is ready, without waiting on the listener", async () => {
    const user = fakeUser()
    const auth = fakeAuth({ currentUser: user })

    await expect(resolveCurrentUser(auth)).resolves.toBe(user)
    expect(onAuthStateChanged).not.toHaveBeenCalled()
  })

  it("waits for the listener to deliver a restored user when currentUser is null at the ready tick", async () => {
    const user = fakeUser()
    const unsubscribe = vi.fn()
    // The SDK fires the listener asynchronously; mirror that so the callback
    // doesn't run before `unsubscribe` is bound in the implementation.
    vi.mocked(onAuthStateChanged).mockImplementation((_auth, next) => {
      queueMicrotask(() => (next as (u: User | null) => void)(user))
      return unsubscribe
    })

    await expect(resolveCurrentUser(fakeAuth({ currentUser: null }))).resolves.toBe(user)
    expect(unsubscribe).toHaveBeenCalledOnce()
  })

  it("resolves null when the restore window elapses with no user (genuinely signed out)", async () => {
    const unsubscribe = vi.fn()
    vi.mocked(onAuthStateChanged).mockImplementation(() => unsubscribe)

    vi.useFakeTimers()
    try {
      const pending = resolveCurrentUser(fakeAuth({ currentUser: null }))
      const assertion = expect(pending).resolves.toBeNull()
      await vi.advanceTimersByTimeAsync(1500)
      await assertion
      expect(unsubscribe).toHaveBeenCalledOnce()
    } finally {
      vi.useRealTimers()
    }
  })
})

// getFirebaseIdToken is what the API client's Authorization header ultimately
// resolves through. It must surface a token only once a user is resolved (never
// a header-less request mid-restore) and resolve null cleanly for a genuinely
// signed-out caller (the legitimate public-endpoint path).
describe("getFirebaseIdToken", () => {
  const fakeUser = (getIdToken: (force?: boolean) => Promise<string>) =>
    ({ uid: "u1", email: "u@x", getIdToken }) as unknown as User

  const fakeAuth = (over: Partial<Auth>): Auth =>
    ({
      authStateReady: vi.fn().mockResolvedValue(undefined),
      currentUser: null,
      ...over,
    }) as unknown as Auth

  beforeEach(() => {
    vi.mocked(onAuthStateChanged).mockReset()
    vi.mocked(getFirebaseAuth).mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("returns the token only after the restore resolves a user — never null mid-transition", async () => {
    const user = fakeUser(() => Promise.resolve("id-token"))
    // currentUser is null at the ready tick and only arrives via the listener,
    // so a token here proves the header waited for the restore instead of
    // going out empty.
    vi.mocked(onAuthStateChanged).mockImplementation((_auth, next) => {
      queueMicrotask(() => (next as (u: User | null) => void)(user))
      return vi.fn()
    })
    vi.mocked(getFirebaseAuth).mockReturnValue(fakeAuth({ currentUser: null }))

    await expect(getFirebaseIdToken()).resolves.toBe("id-token")
  })

  it("returns null when no user is signed in, leaving the public-endpoint path intact", async () => {
    vi.mocked(onAuthStateChanged).mockImplementation(() => vi.fn())
    vi.mocked(getFirebaseAuth).mockReturnValue(fakeAuth({ currentUser: null }))

    vi.useFakeTimers()
    try {
      const pending = getFirebaseIdToken()
      const assertion = expect(pending).resolves.toBeNull()
      await vi.advanceTimersByTimeAsync(1500)
      await assertion
    } finally {
      vi.useRealTimers()
    }
  })

  it("forces a fresh token past the SDK cache when asked", async () => {
    const getIdToken = vi.fn().mockResolvedValue("fresh-token")
    vi.mocked(getFirebaseAuth).mockReturnValue(fakeAuth({ currentUser: fakeUser(getIdToken) }))

    await expect(getFirebaseIdToken(true)).resolves.toBe("fresh-token")
    expect(getIdToken).toHaveBeenCalledWith(true)
  })
})
