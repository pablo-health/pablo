// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Image from "next/image"
import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { legacyLaunchUri, clickThroughAnchor } from "@/lib/companionLaunch"

/**
 * Companion launch fallback page.
 *
 * The primary handoff is a domain-verified Universal Link / App URI Handler
 * — when the OS honors it, the companion takes over and the browser never
 * loads this page. This page exists for the case where the OS did NOT hand
 * off (the verified link fell through to the web, e.g. Firefox, or the app
 * isn't installed): it gives the user a path forward by firing the legacy
 * `pablohealth://session/start?intent=<id>` scheme.
 *
 * Security: this page holds ONLY the opaque, single-use intent id from the
 * route param. It never calls `/launch/redeem` (only the companion does) and
 * never displays patient data. The intent id is worthless to anyone but the
 * device authenticated as the user who issued it.
 */
export default function LaunchFallbackPage({
  params,
}: {
  params: Promise<{ intentId: string }>
}) {
  // App Router hands `params` as a Promise; resolve it into state rather than
  // `use()` so the page renders its shell immediately (no Suspense boundary
  // needed) and the auto-fire effect runs once the id is known.
  const [intentId, setIntentId] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    void params.then(({ intentId }) => {
      if (active) setIntentId(intentId)
    })
    return () => {
      active = false
    }
  }, [params])

  useEffect(() => {
    if (!intentId) return
    // Give the OS a beat to route the verified link first; if we're still
    // here, fire the legacy scheme to wake the companion.
    const timer = window.setTimeout(() => {
      clickThroughAnchor(legacyLaunchUri(intentId))
    }, 1200)
    return () => window.clearTimeout(timer)
  }, [intentId])

  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-6 text-center">
      <Image
        src="/pablo-tie.webp"
        alt="Pablo bear"
        width={72}
        height={72}
        priority
      />
      <h1 className="mt-4 font-display text-xl font-semibold text-neutral-900">
        Opening Pablo Companion…
      </h1>
      <p className="mt-2 max-w-sm text-sm text-neutral-600">
        If the desktop app doesn&apos;t open automatically, use the button
        below.
      </p>

      <div className="mt-6 flex flex-col items-center gap-3">
        <Button
          disabled={!intentId}
          onClick={() =>
            intentId && clickThroughAnchor(legacyLaunchUri(intentId))
          }
        >
          Open Pablo Companion
        </Button>
        <a
          href="https://pablo.health"
          target="_blank"
          rel="noreferrer"
          className="text-sm text-primary-700 hover:underline"
        >
          Don&apos;t have it? Download
        </a>
      </div>
    </main>
  )
}
