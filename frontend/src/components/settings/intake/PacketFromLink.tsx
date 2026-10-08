// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect } from "react"
import { useSearchParams } from "next/navigation"

/**
 * Opens the packet a link names (`?packet=<id>`), so a page that just made
 * one can send the practice straight to it.
 *
 * Its own component, behind its own Suspense boundary in the card, because
 * reading search params suspends a client render. And read through the
 * router rather than `window.location` on first render: on an in-app link
 * the card renders before the address bar changes, so a one-time read saw
 * the page the practice came from and opened nothing.
 */
export function PacketFromLink({ onPacket }: { onPacket: (templateId: string) => void }) {
  const asked = useSearchParams().get("packet")
  useEffect(() => {
    if (asked) onPacket(asked)
  }, [asked, onPacket])
  return null
}
