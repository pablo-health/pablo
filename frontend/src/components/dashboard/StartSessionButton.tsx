// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { createLaunchIntent } from "@/lib/api/devices"
import {
  armNoHandoffFallback,
  clickThroughAnchor,
  legacyLaunchUri,
} from "@/lib/companionLaunch"

interface StartSessionButtonProps {
  appointmentId: string
}

interface ReadyIntent {
  intentId: string
  launchUrl: string
}

/**
 * "Start Session" — hands the appointment off to the enrolled companion via
 * a domain-verified deep link.
 *
 * Flow (per docs/design/companion-thin-client.md):
 *  1. Prefetch the launch intent on hover/focus so the rendered anchor already
 *     has its `launch_url` href when the user clicks. macOS Safari only routes
 *     a Universal Link when the navigation originates from a *real*,
 *     user-activated anchor click — NOT from a `window.location` assignment,
 *     and NOT from a synthetic click fired after an `await` (the user-gesture
 *     context is gone once we round-trip the network). Letting the actual
 *     anchor click drive the navigation is the contract's "preferred" form.
 *  2. On click, with the intent already in hand, the browser navigates to
 *     `launch_url` via the real anchor and we arm a ~1.5s no-handoff timer.
 *     If the companion took over, the page is backgrounded and we cancel.
 *     Otherwise (Firefox / nothing installed) fall back to the legacy
 *     `pablohealth://session/start?intent=<id>` scheme — the SAME single-use
 *     intent, never a second POST.
 *  3. If the click lands before the prefetch resolved (rare: keyboard activate
 *     with no prior focus event, or a slow round-trip), we fetch on click as a
 *     fallback. Safari may not route the Universal Link in that case, but the
 *     legacy no-handoff fallback still delivers the session.
 */
export function StartSessionButton({ appointmentId }: StartSessionButtonProps) {
  // The prefetched intent, if any. `null` until the first hover/focus or click.
  const [intent, setIntent] = useState<ReadyIntent | null>(null)
  // True from click until the no-handoff window settles, so a rapid second
  // click can't issue a second intent or orphan the first fallback timer.
  const [busy, setBusy] = useState(false)

  const fetchingRef = useRef(false)
  const cleanupRef = useRef<(() => void) | null>(null)

  // Cancel any in-flight no-handoff timer on unmount.
  useEffect(() => {
    return () => cleanupRef.current?.()
  }, [])

  // Run (and clear) any previously-armed fallback before arming a new one, so
  // an earlier timer is never orphaned outside `cleanupRef`.
  const clearFallback = useCallback(() => {
    cleanupRef.current?.()
    cleanupRef.current = null
  }, [])

  // Lazily issue the launch intent so the anchor has a real href at click
  // time. Idempotent: only the first hover/focus actually POSTs.
  const prefetchIntent = useCallback(async (): Promise<ReadyIntent | null> => {
    if (intent) return intent
    if (fetchingRef.current) return null
    fetchingRef.current = true
    try {
      const { intent_id, launch_url } = await createLaunchIntent(appointmentId)
      const ready = { intentId: intent_id, launchUrl: launch_url }
      setIntent(ready)
      return ready
    } catch {
      // Intent issuance failed (backend flag off, network, not-authorized).
      // Leave today's behavior — the anchor stays inert; no launch attempt.
      return null
    } finally {
      fetchingRef.current = false
    }
  }, [appointmentId, intent])

  // Arm the no-handoff fallback for a given intent and hold `busy` for the
  // full window so the button can't be re-triggered mid-handoff.
  const armFallback = useCallback(
    (intentId: string) => {
      clearFallback()
      setBusy(true)
      cleanupRef.current = armNoHandoffFallback(() => {
        clickThroughAnchor(legacyLaunchUri(intentId))
        cleanupRef.current = null
        setBusy(false)
      })
    },
    [clearFallback],
  )

  const onClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (busy) {
      e.preventDefault()
      return
    }
    if (intent) {
      // Real, user-activated anchor click → Safari routes the Universal Link
      // via the default navigation. Don't preventDefault; just arm the timer.
      armFallback(intent.intentId)
      return
    }
    // No prefetched intent yet — fetch on click as a fallback. This breaks the
    // user-gesture chain for the verified link, but the legacy no-handoff
    // fallback still delivers the session.
    e.preventDefault()
    setBusy(true)
    void prefetchIntent().then((ready) => {
      if (!ready) {
        setBusy(false)
        return
      }
      clickThroughAnchor(ready.launchUrl)
      armFallback(ready.intentId)
    })
  }

  return (
    <Button
      asChild
      size="sm"
      aria-disabled={busy || undefined}
      onPointerEnter={() => void prefetchIntent()}
      onFocus={() => void prefetchIntent()}
    >
      <a
        href={intent?.launchUrl ?? "#"}
        rel="noopener"
        onClick={onClick}
      >
        Start session
      </a>
    </Button>
  )
}
