// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { signOut } from "firebase/auth"
import { getFirebaseAuth } from "@/lib/firebase"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

const IDLE_TIMEOUT_MS = 15 * 60 * 1000 // 15 minutes (HIPAA / CMS standard)
const WARNING_BEFORE_MS = 2 * 60 * 1000 // Warn 2 minutes before logout

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
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null)
  const lastActivityRef = useRef(0)
  const throttleRef = useRef(0)
  const signingOutRef = useRef(false)

  // Initialize on mount (avoids impure Date.now() during render)
  useEffect(() => {
    lastActivityRef.current = Date.now()
  }, [])

  const performSignOut = useCallback(async () => {
    if (signingOutRef.current) return
    signingOutRef.current = true
    try {
      await signOut(getFirebaseAuth())
    } catch {
      // Firebase not initialized (dev mode)
    }
    await fetch("/api/logout")
    router.push("/login?reason=idle_timeout")
  }, [router])

  const handleStaySignedIn = useCallback(() => {
    if (signingOutRef.current) return
    lastActivityRef.current = Date.now()
    setSecondsLeft(null)
  }, [])

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

  // Check idle state every second (uses Date.now so browser tab throttling is safe)
  useEffect(() => {
    const interval = setInterval(() => {
      const remaining = IDLE_TIMEOUT_MS - (Date.now() - lastActivityRef.current)

      if (remaining <= 0) {
        performSignOut()
        return
      }

      setSecondsLeft(
        remaining <= WARNING_BEFORE_MS ? Math.ceil(remaining / 1000) : null,
      )
    }, 1000)

    return () => clearInterval(interval)
  }, [performSignOut])

  if (secondsLeft === null) return null

  const minutes = Math.floor(secondsLeft / 60)
  const secs = secondsLeft % 60

  return (
    <Dialog open onOpenChange={(open) => !open && handleStaySignedIn()}>
      <DialogContent
        showCloseButton={false}
        onInteractOutside={(e) => e.preventDefault()}
        className="sm:max-w-md"
      >
        <DialogHeader>
          <DialogTitle>Session Expiring</DialogTitle>
          <DialogDescription>
            You will be signed out in{" "}
            <span className="font-mono font-semibold text-neutral-900">
              {minutes}:{secs.toString().padStart(2, "0")}
            </span>{" "}
            due to inactivity.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <button
            onClick={handleStaySignedIn}
            className="w-full bg-primary-600 text-white px-6 py-2.5 rounded-lg font-medium hover:bg-primary-700 active:scale-[0.98] transition-all duration-200"
          >
            Stay Signed In
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
