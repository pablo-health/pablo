// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One section of the portal on a page of its own: `/portal/{slug}/{id}`.
 *
 * An id nothing registered, or a section whose module this practice has
 * off, goes back to Home rather than showing an error: an old bookmark or a
 * hand-typed address is not a mistake worth a screen. Replaced rather than
 * pushed, so Back does not return to the address that went nowhere.
 *
 * A section behind a module waits for the capability document before it
 * draws, so a practice that has the module off never flashes it. A document
 * that never arrives draws it anyway — the same direction
 * `visiblePortalSlots` fails in, and for the same reason.
 */

"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import { ResolvingCard } from "./PortalAuthCards"
import { usePortalView } from "./context"
import { getPortalSlots } from "./slots"

export function PortalSection({ id }: { id: string }) {
  const view = usePortalView()
  const router = useRouter()

  const registered = getPortalSlots().find((slot) => slot.id === id)
  const waiting = registered?.module !== undefined && view.capabilities.status === "loading"
  const served = view.slots.find((slot) => slot.id === id)
  const goHome = !waiting && served === undefined

  useEffect(() => {
    if (goHome) router.replace(view.base)
  }, [goHome, router, view.base])

  if (waiting || served === undefined) return <ResolvingCard />

  const { Component } = served
  return (
    <section data-testid={`portal-section-${id}`} aria-label={served.label}>
      <Component slug={view.slug} sessionToken={view.sessionToken} />
    </section>
  )
}
