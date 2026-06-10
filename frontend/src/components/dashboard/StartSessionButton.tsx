// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useRef, useState } from "react"
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

/**
 * "Start Session" — hands the appointment off to the enrolled companion via
 * a domain-verified deep link.
 *
 * Flow (per docs/design/companion-thin-client.md):
 *  1. On click, POST /api/launch/intent → { launch_url, intent_id }.
 *  2. Synchronously (still inside the user gesture) navigate via a real
 *     anchor click to `launch_url` — macOS Safari only routes a Universal
 *     Link from an actual click, not a `window.location` assignment.
 *  3. Arm a ~1.5s no-handoff timer. If the companion took over, the page is
 *     backgrounded and we cancel. Otherwise (Firefox / nothing installed)
 *     fall back to the legacy `pablohealth://session/start?intent=<id>`
 *     scheme — the SAME single-use intent, never a second POST.
 */
export function StartSessionButton({ appointmentId }: StartSessionButtonProps) {
  const [pending, setPending] = useState(false)
  const cleanupRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    return () => cleanupRef.current?.()
  }, [])

  const onClick = async () => {
    if (pending) return
    setPending(true)
    try {
      const { intent_id, launch_url } = await createLaunchIntent(appointmentId)
      // Primary: domain-verified link via a real (synthetic) anchor click.
      clickThroughAnchor(launch_url)
      // Fallback to the legacy scheme if the OS didn't hand off. Reuses the
      // same intent (still valid within its TTL; single-use means whichever
      // path the OS delivers wins and the other is a no-op 410).
      cleanupRef.current = armNoHandoffFallback(() => {
        clickThroughAnchor(legacyLaunchUri(intent_id))
      })
    } catch {
      // Intent issuance failed (backend flag off, network, not-authorized).
      // Leave today's behavior: no launch, button simply re-enables.
    } finally {
      setPending(false)
    }
  }

  return (
    <Button size="sm" onClick={onClick} disabled={pending}>
      Start session
    </Button>
  )
}
