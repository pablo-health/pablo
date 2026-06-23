// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Firebase implementation of the core client-side {@link ClientAuthProvider}
 * contract: live auth state, id-token resolution, and full sign-out.
 *
 * This module IS the `firebase` provider — the rest of the app reaches it
 * only through `@/lib/auth/provider`, never directly.
 */

import { useEffect, useState } from "react"
import {
  onAuthStateChanged,
  onIdTokenChanged,
  signOut as firebaseSdkSignOut,
  type Auth,
  type User,
} from "firebase/auth"

import { getFirebaseAuth, initFirebase } from "@/lib/firebase"
import { useConfig } from "@/lib/config"
import { clearFirebaseAuthStorage } from "@/lib/firebaseAuthRecovery"
import type { AuthState, AuthUser, ClientAuthProvider } from "@/lib/auth/types"

/**
 * Upper bound on how long {@link resolveCurrentUser} will wait for Firebase
 * to deliver a restored user after ``authStateReady()`` returned null.
 * Covers two restoration races (see ``resolveCurrentUser``); the window is
 * generous because the cost only lands on requests that would have failed
 * without it (genuinely signed-out callers don't reach the API client).
 */
const AUTH_RESTORE_WAIT_MS = 1500

function toAuthUser(user: User): AuthUser {
  return {
    uid: user.uid,
    email: user.email,
    displayName: user.displayName,
    photoURL: user.photoURL,
  }
}

/**
 * Resolve Firebase's ``currentUser`` for an authenticated request.
 *
 * ``authStateReady()`` resolves on the first auth-state-listener tick and
 * covers the common case. Two restoration races land here with a null
 * ``currentUser`` and a user who is in fact signed in:
 *
 *   1. ``multiFactor.enroll()`` briefly tears down the pre-MFA user and
 *      signs the post-MFA user back in. A request fired in that gap sees
 *      ``currentUser`` null even though the post-MFA user is about to
 *      install — surfaced by pablo#307.
 *   2. A hard navigation (``page.goto`` in Playwright, a real reload in a
 *      browser tab) reinits the SDK. The first ``authStateReady`` tick can
 *      fire before IndexedDB persistence is observed, so ``currentUser`` is
 *      null for a few hundred ms after restoration starts — surfaced by
 *      chart-render-smoke@dev on 2026-05-28.
 *
 * Both races resolve within tens to hundreds of milliseconds. We give
 * ``onAuthStateChanged`` up to ``AUTH_RESTORE_WAIT_MS`` to deliver a
 * non-null user before giving up; if the user really is signed out we
 * resolve null and the caller proceeds without an ``Authorization`` header
 * (the backend's 401 is the right answer).
 */
export async function resolveCurrentUser(auth: Auth): Promise<User | null> {
  await auth.authStateReady()
  if (auth.currentUser) return auth.currentUser
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      unsubscribe()
      resolve(auth.currentUser)
    }, AUTH_RESTORE_WAIT_MS)
    const unsubscribe = onAuthStateChanged(auth, (user) => {
      if (user) {
        clearTimeout(timer)
        unsubscribe()
        resolve(user)
      }
    })
  })
}

/**
 * Upper bound on the boot-time auth-state sync (token refresh + cookie
 * write). A restored session whose refresh token has expired makes
 * ``getIdToken()`` reach out to Firebase, and the cookie sync is a network
 * call too; either can stall. The splash must never outlive this, so the
 * race below rejects past the deadline and the caller falls back to a
 * clean signed-out state.
 */
const AUTH_SYNC_TIMEOUT_MS = 10_000

/**
 * Reject ``promise`` if it has not settled within ``ms``. The timer is
 * cleared on settle so a resolved promise leaves nothing pending.
 */
export function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms)
  })
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer))
}

/**
 * Reset a wedged Firebase session: sign the SDK out, wipe the persisted
 * auth record the SDK restores from on the next load, and clear the SSR
 * cookie. Best-effort throughout — the caller has already decided to treat
 * the user as signed out, so a failure on any step must not propagate.
 */
export async function clearStaleSession(auth: Auth): Promise<void> {
  try {
    await firebaseSdkSignOut(auth)
  } catch {
    // SDK may already be wedged; we wipe its storage next regardless.
  }
  await clearFirebaseAuthStorage()
  try {
    await fetch("/api/logout")
  } catch {
    // Best-effort cookie clear.
  }
}

/**
 * Reconcile a single ``onIdTokenChanged`` tick into the provider-neutral
 * user and the SSR session cookie. Extracted from the hook so the
 * credential-vs-cookie failure handling below can be unit tested without
 * rendering. The caller owns the ``loading`` lifecycle (always cleared in a
 * ``finally``); a throw here is a bug, since every awaited call is guarded.
 */
export async function syncAuthTick(
  auth: Auth,
  firebaseUser: User | null,
  setUser: (user: AuthUser | null) => void,
): Promise<void> {
  if (!firebaseUser) {
    setUser(null)
    try {
      await fetch("/api/logout")
    } catch (err) {
      console.warn("Session cookie clear failed:", err)
    }
    return
  }

  let idToken: string
  try {
    idToken = await withTimeout(firebaseUser.getIdToken(), AUTH_SYNC_TIMEOUT_MS, "getIdToken")
  } catch (err) {
    // The SDK restored a session but can't mint a token — the refresh token
    // is expired or revoked. Drop the stale record so the next sign-in
    // starts clean instead of wedging the boot.
    console.warn("Firebase token refresh failed; clearing stale session:", err)
    setUser(null)
    await clearStaleSession(auth)
    return
  }

  // Sync the SSR session cookie before surfacing the user (middleware gates
  // /dashboard on it). A failed sync — commonly an in-flight request aborted
  // by navigation — is non-fatal: the SDK session is still valid and the next
  // token tick re-syncs, so keep the user rather than signing them out.
  try {
    await withTimeout(
      fetch("/api/login", {
        method: "POST",
        headers: { Authorization: `Bearer ${idToken}` },
      }),
      AUTH_SYNC_TIMEOUT_MS,
      "/api/login",
    )
  } catch (err) {
    console.warn("Session cookie sync failed; will retry on next token change:", err)
  }
  setUser(toAuthUser(firebaseUser))
}

/**
 * Live auth state for the React context. Initializes Firebase from the
 * runtime config, then mirrors ``onIdTokenChanged`` into a provider-neutral
 * {@link AuthUser} and syncs the token to the server session cookie
 * (the ``next-firebase-auth-edge`` cookie that SSR + middleware read).
 *
 * The token refresh and cookie sync are awaited before reporting the user
 * because route-protection middleware gates ``/dashboard`` on that cookie —
 * surfacing the user any earlier races the redirect ahead of the cookie and
 * bounces back to ``/login``. Both steps are network calls and are bounded by
 * ``withTimeout``, and ``loading`` always clears in ``finally`` so the boot
 * can never wedge on the splash (which it did before: an un-caught
 * ``getIdToken()`` rejection killed the listener and left ``loading`` true,
 * recoverable only by clearing site data — the SDK's own stuck-state recovery
 * catches only one specific internal assertion, not a rejected refresh).
 *
 * The two failures are handled differently because they mean different
 * things. A rejected ``getIdToken()`` means the restored refresh token is
 * expired or revoked — the credential is dead, so drop the stale session and
 * let the user sign in fresh. A failed cookie sync is usually just an
 * in-flight ``fetch`` aborted by navigation; the SDK session is still valid
 * and the next token tick re-syncs, so it must NOT tear down the session
 * (doing so signed users out on a transient blip and tripped the smoke
 * console-error guard). Both are handled, self-healing conditions, so they
 * log at ``warn`` — ``error`` is reserved for failures the user must act on.
 */
export function useFirebaseAuthState(): AuthState {
  const config = useConfig()
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(!config.devMode)

  useEffect(() => {
    if (config.devMode) return

    const auth = initFirebase({
      apiKey: config.firebaseApiKey,
      authDomain: config.firebaseAuthDomain,
      projectId: config.firebaseProjectId,
      appId: config.firebaseAppId,
    })

    return onIdTokenChanged(auth, async (firebaseUser) => {
      try {
        await syncAuthTick(auth, firebaseUser, setUser)
      } finally {
        // Always clear loading — a thrown sync must never wedge the splash.
        setLoading(false)
      }
    })
  }, [config])

  return { user, loading }
}

/**
 * The raw Firebase ``User``, for Firebase-specific surfaces that need SDK
 * details the neutral {@link AuthUser} deliberately omits (e.g. TOTP
 * enrollment inspecting ``providerData`` / ``multiFactor``). Firebase-only;
 * the OIDC provider hosts MFA on Keycloak's pages and has no analogue.
 */
export function useFirebaseUser(): User | null {
  const config = useConfig()
  const [user, setUser] = useState<User | null>(null)

  useEffect(() => {
    if (config.devMode) return
    // initFirebase is idempotent — safe even though useFirebaseAuthState
    // (mounted above in the tree) has typically already initialized it.
    const auth = initFirebase({
      apiKey: config.firebaseApiKey,
      authDomain: config.firebaseAuthDomain,
      projectId: config.firebaseProjectId,
      appId: config.firebaseAppId,
    })
    return onIdTokenChanged(auth, setUser)
  }, [config])

  return user
}

/**
 * Resolve the current id token, force-refreshing past Firebase's cached
 * token when asked (the retry-on-401 path). Throws if Firebase is not yet
 * initialized; the API client treats that as "no token".
 */
export async function getFirebaseIdToken(forceRefresh = false): Promise<string | null> {
  const auth = getFirebaseAuth()
  const currentUser = await resolveCurrentUser(auth)
  return currentUser ? currentUser.getIdToken(forceRefresh) : null
}

/**
 * Full sign-out: clear the Firebase SDK session and the server session
 * cookie. Best-effort on both — a caller still redirects afterward.
 *
 * With `wipePersisted`, also delete the IndexedDB record the SDK restores
 * from on the next load. `firebaseSdkSignOut` clears the in-memory user but
 * a tab restored from bfcache (notably iOS Safari) can re-hydrate the old
 * session — and with it the original `auth_time` — straight back in. The
 * idle-timeout path needs the persisted record gone so re-login mints a
 * fresh `auth_time` instead of looping on the server idle check.
 */
export async function firebaseSignOut(opts?: {
  wipePersisted?: boolean
}): Promise<void> {
  try {
    await firebaseSdkSignOut(getFirebaseAuth())
  } catch {
    // Firebase not initialized (dev mode) — still clear the server cookie.
  }
  if (opts?.wipePersisted) {
    await clearFirebaseAuthStorage()
  }
  try {
    await fetch("/api/logout")
  } catch {
    // Best-effort: a redirect still follows even if the cookie clear fails.
  }
}

export const firebaseClientProvider: ClientAuthProvider = {
  id: "firebase",
  useAuthState: useFirebaseAuthState,
  getIdToken: getFirebaseIdToken,
  signOut: firebaseSignOut,
}
