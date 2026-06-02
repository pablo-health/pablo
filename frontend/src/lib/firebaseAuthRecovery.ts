// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Recovers from the Firebase Auth "INTERNAL ASSERTION FAILED: Pending
 * promise was never set" stuck-state.
 *
 * Background: when signInWithRedirect or signInWithPopup is interrupted
 * (popup closed mid-flow, tab refresh during MFA, navigation away,
 * service worker eviction), the Firebase Auth SDK can leave an
 * unfinished auth event record in IndexedDB (`firebaseLocalStorageDb`).
 * On the next page load the SDK reads that record and fires
 * `onAuthEvent` to resume processing — but the JS callback that was
 * waiting on it died with the previous page context. No pendingPromise
 * is registered, the SDK's internal assertion fires, and every
 * subsequent sign-in attempt hits the same stale event before it can
 * register a fresh one. Without DevTools the user is permanently stuck
 * on /login. THERAPY-n1n6.
 *
 * Recovery: detect the specific assertion via a window rejection/error
 * listener, wipe `firebaseLocalStorageDb`, and reload with a session
 * flag so the page can show a one-line notice. Specific-error match
 * keeps this from swallowing unrelated Firebase failures.
 */

const STUCK_STATE_FLAG_KEY = "pablo:auth-recovered"
const FIREBASE_AUTH_DB = "firebaseLocalStorageDb"

export function isFirebaseStuckStateError(err: unknown): boolean {
  if (!err) return false
  const candidate =
    (err as Error)?.message ??
    (err as { reason?: { message?: string } })?.reason?.message ??
    err
  const msg = String(candidate)
  return /INTERNAL ASSERTION FAILED.*Pending promise was never set/i.test(msg)
}

export async function clearFirebaseAuthStorage(): Promise<void> {
  if (typeof indexedDB === "undefined") return
  await new Promise<void>((resolve) => {
    let settled = false
    const finish = () => {
      if (settled) return
      settled = true
      resolve()
    }
    try {
      const req = indexedDB.deleteDatabase(FIREBASE_AUTH_DB)
      req.onsuccess = finish
      req.onerror = finish
      req.onblocked = finish
    } catch {
      finish()
    }
  })
}

let installed = false

/**
 * Install a window-level rejection + error listener that auto-clears
 * the stuck state and reloads the page. Subsequent calls are no-ops.
 * Safe to call from any client component that mounts on the auth path
 * (login, MFA enrollment, BAA acceptance).
 */
export function installAuthRecovery(): void {
  if (installed || typeof window === "undefined") return
  installed = true

  const recover = () => {
    sessionStorage.setItem(STUCK_STATE_FLAG_KEY, "1")
    void clearFirebaseAuthStorage().then(() => {
      window.location.reload()
    })
  }

  window.addEventListener("unhandledrejection", (event) => {
    if (!isFirebaseStuckStateError(event.reason)) return
    event.preventDefault()
    recover()
  })

  window.addEventListener("error", (event) => {
    if (!isFirebaseStuckStateError(event.error)) return
    event.preventDefault()
    recover()
  })
}

/**
 * Read-and-clear the recovery flag set by installAuthRecovery() before
 * the last reload. Returns true once per recovery cycle; subsequent
 * calls in the same page return false. The login page calls this on
 * mount to decide whether to surface "we cleared a stuck sign-in state"
 * to the user.
 */
export function consumeRecoveryNotice(): boolean {
  if (typeof window === "undefined") return false
  const flag = sessionStorage.getItem(STUCK_STATE_FLAG_KEY)
  if (flag) sessionStorage.removeItem(STUCK_STATE_FLAG_KEY)
  return flag === "1"
}
