// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useQueryClient } from "@tanstack/react-query"
import { signOutAndClear } from "@/lib/auth/signOutAndClear"
import { handleTerminalAuthLogout, returnToParam } from "@/lib/api/client"
import { getSessionStatus, touchSession } from "@/lib/api/session"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

/**
 * Idle-session controller.
 *
 * The backend's Redis idle clock (backend/app/auth/idle_session.py) is the
 * single source of truth for whether a session is alive. This component:
 *
 *  - validates the session against `GET /api/auth/session` on mount, on
 *    tab restore (pageshow / visibilitychange), and on a poll — a restored
 *    tab is exactly the case where a fresh local timer would happily show
 *    PHI over a session the backend tombstoned long ago;
 *  - drives the warning dialog's countdown from the *server's* remaining
 *    time, not a parallel local clock;
 *  - keeps locally-active users alive server-side via a throttled
 *    `POST /api/auth/session/touch` (typing a long note generates no API
 *    traffic on its own — without the touch, the eventual save would 401);
 *  - boots through the same forced-logout flow as apiClient when the
 *    server says the session is dead.
 *
 * When the server reports enforcement off (dev mode / no Redis — e.g. a
 * single-instance self-host), the original local activity clock governs,
 * unchanged: 15 minutes of inactivity → warning → sign-out.
 */

const IDLE_TIMEOUT_MS = 15 * 60 * 1000 // local-fallback clock (HIPAA / CMS standard)
const WARNING_BEFORE_MS = 2 * 60 * 1000 // Warn 2 minutes before logout
const PEEK_INTERVAL_MS = 45 * 1000 // server poll cadence
const WARNING_PEEK_INTERVAL_MS = 10 * 1000 // faster poll inside the warning window
const ACTIVITY_TOUCH_INTERVAL_MS = 4 * 60 * 1000 // keep-alive cadence while active
const EXPIRY_GRACE_MS = 30 * 1000 // local backstop if the confirming peek can't land

const ACTIVITY_EVENTS: (keyof DocumentEventMap)[] = [
  "mousemove",
  "keydown",
  "mousedown",
  "touchstart",
  "scroll",
]
const THROTTLE_MS = 1000

export function IdleTimeout() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null)
  const lastActivityRef = useRef(0)
  const throttleRef = useRef(0)
  const signingOutRef = useRef(false)

  // Server-clock state. `enforced` null = no successful peek yet — the
  // local clock governs until the server has answered once.
  const serverEnforcedRef = useRef<boolean | null>(null)
  const expiresAtRef = useRef<number | null>(null)
  const lastPeekAtRef = useRef(0)
  const lastTouchAtRef = useRef(0)
  const peekInFlightRef = useRef(false)
  const touchInFlightRef = useRef(false)

  // Initialize on mount (avoids impure Date.now() during render)
  useEffect(() => {
    lastActivityRef.current = Date.now()
    lastTouchAtRef.current = Date.now()
  }, [])

  const performLocalSignOut = useCallback(async () => {
    if (signingOutRef.current) return
    signingOutRef.current = true
    await signOutAndClear(queryClient, router, `/login?reason=idle_timeout${returnToParam()}`)
  }, [router, queryClient])

  /**
   * Ask the backend whether this session is still alive. Read-only — the
   * peek never extends the session. Boots on a dead session; transient
   * failures keep the current mode and retry on the next poll (a terminal
   * 401 from the peek itself already boots via apiClient).
   */
  const validateSession = useCallback(async () => {
    if (peekInFlightRef.current || signingOutRef.current) return
    peekInFlightRef.current = true
    lastPeekAtRef.current = Date.now()
    try {
      const status = await getSessionStatus()
      serverEnforcedRef.current = status.enforced
      if (!status.enforced) return
      if (!status.active) {
        signingOutRef.current = true
        handleTerminalAuthLogout("idle_timeout")
        return
      }
      expiresAtRef.current = Date.now() + (status.seconds_remaining ?? 0) * 1000
    } catch {
      // Network / 5xx — nothing to conclude about the session.
    } finally {
      peekInFlightRef.current = false
    }
  }, [])

  /** Explicit keep-alive: refresh the server's idle heartbeat. */
  const touchServerSession = useCallback(async () => {
    if (touchInFlightRef.current || signingOutRef.current) return
    touchInFlightRef.current = true
    lastTouchAtRef.current = Date.now()
    try {
      const status = await touchSession()
      serverEnforcedRef.current = status.enforced
      if (status.enforced && status.active) {
        expiresAtRef.current = Date.now() + (status.seconds_remaining ?? 0) * 1000
      }
    } catch {
      // An already-dead session 401s and boots via apiClient; transient
      // failures are retried on the next keep-alive interval.
    } finally {
      touchInFlightRef.current = false
    }
  }, [])

  // Validate on entry and whenever the tab comes back to life. The
  // restored-tab path (browser relaunch, bfcache) is the one where a
  // purely local timer restarts fresh while the backend session may be
  // long dead — this is what puts the user back on /login instead of a
  // logged-in-looking shell.
  useEffect(() => {
    void validateSession()
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void validateSession()
    }
    const onPageShow = () => void validateSession()
    document.addEventListener("visibilitychange", onVisibilityChange)
    window.addEventListener("pageshow", onPageShow)
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange)
      window.removeEventListener("pageshow", onPageShow)
    }
  }, [validateSession])

  const handleStaySignedIn = useCallback(() => {
    if (signingOutRef.current) return
    lastActivityRef.current = Date.now()
    if (serverEnforcedRef.current) {
      // The button's promise ("stay signed in") must reach the backend
      // clock, not just the local one.
      void touchServerSession()
    }
    setSecondsLeft(null)
  }, [touchServerSession])

  // Track user activity — ignored once the warning dialog is showing
  useEffect(() => {
    const onActivity = () => {
      if (secondsLeft !== null) return
      const now = Date.now()
      if (now - throttleRef.current < THROTTLE_MS) return
      throttleRef.current = now
      lastActivityRef.current = now
    }

    for (const event of ACTIVITY_EVENTS) {
      document.addEventListener(event, onActivity, { passive: true })
    }
    return () => {
      for (const event of ACTIVITY_EVENTS) {
        document.removeEventListener(event, onActivity)
      }
    }
  }, [secondsLeft])

  // 1s tick: drives the countdown, the server poll, and the keep-alive.
  // Date.now()-based so browser tab throttling is safe.
  useEffect(() => {
    const interval = setInterval(() => {
      if (signingOutRef.current) return
      const now = Date.now()
      const serverMode =
        serverEnforcedRef.current === true && expiresAtRef.current !== null

      let remaining: number
      if (serverMode) {
        remaining = (expiresAtRef.current as number) - now
        const peekInterval =
          remaining <= WARNING_BEFORE_MS ? WARNING_PEEK_INTERVAL_MS : PEEK_INTERVAL_MS
        if (remaining <= 0 || now - lastPeekAtRef.current >= peekInterval) {
          // At/past expiry the peek is the arbiter — clock skew aside,
          // another tab's API traffic may have kept the session alive.
          // It boots (hard, cookie-clearing) when the session is dead.
          void validateSession()
        }
        if (remaining <= -EXPIRY_GRACE_MS) {
          // The confirming peek can't land (backend unreachable) — don't
          // keep showing PHI past the window; sign out locally.
          void performLocalSignOut()
          return
        }
        if (
          secondsLeft === null &&
          lastActivityRef.current > lastTouchAtRef.current &&
          now - lastTouchAtRef.current >= ACTIVITY_TOUCH_INTERVAL_MS
        ) {
          void touchServerSession()
        }
      } else {
        remaining = IDLE_TIMEOUT_MS - (now - lastActivityRef.current)
        if (remaining <= 0) {
          void performLocalSignOut()
          return
        }
      }

      setSecondsLeft(
        remaining <= WARNING_BEFORE_MS ? Math.max(0, Math.ceil(remaining / 1000)) : null,
      )
    }, 1000)

    return () => clearInterval(interval)
  }, [performLocalSignOut, validateSession, touchServerSession, secondsLeft])

  if (secondsLeft === null) return null

  // Once the countdown hits 0 the backend session is gone (or about to be
  // confirmed dead by the arbiter peek / expiry grace). "Stay Signed In" can
  // no longer keep it alive, so switch the dialog to an expired state whose
  // button routes to /login instead of leaving the user stuck at 0:00.
  const isExpired = secondsLeft <= 0
  const minutes = Math.floor(secondsLeft / 60)
  const secs = secondsLeft % 60

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (open) return
        if (isExpired) void performLocalSignOut()
        else handleStaySignedIn()
      }}
    >
      <DialogContent
        showCloseButton={false}
        onInteractOutside={(e) => e.preventDefault()}
        className="sm:max-w-md"
      >
        <DialogHeader>
          <DialogTitle>{isExpired ? "Session Expired" : "Session Expiring"}</DialogTitle>
          <DialogDescription>
            {isExpired ? (
              "Your session has expired due to inactivity. Sign in again to continue."
            ) : (
              <>
                You will be signed out in{" "}
                <span className="font-mono font-semibold text-neutral-900">
                  {minutes}:{secs.toString().padStart(2, "0")}
                </span>{" "}
                due to inactivity.
              </>
            )}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <button
            onClick={isExpired ? () => void performLocalSignOut() : handleStaySignedIn}
            className="w-full bg-primary-600 text-white px-6 py-2.5 rounded-lg font-medium hover:bg-primary-700 active:scale-[0.98] transition-all duration-200"
          >
            {isExpired ? "Sign In" : "Stay Signed In"}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
