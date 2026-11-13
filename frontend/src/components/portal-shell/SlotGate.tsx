// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Applies a slot's `Gate`, if it has one, around one of its three places:
 * its tile on Home, its link in the navigation, or its section page.
 *
 * A slot without a gate renders `children` as they are, so the shell draws
 * exactly what it did before gates existed. A gated slot with no session to
 * hand its gate renders nothing: the gate has no way to answer, and a
 * section that cannot say whether it is on stays hidden.
 */

"use client"

import type { ReactNode } from "react"
import type { PortalSlot, PortalSlotProps } from "./slots"

export function SlotGate({
  slot,
  session,
  children,
}: {
  slot: PortalSlot
  session: PortalSlotProps | null
  children: ReactNode
}) {
  const { Gate } = slot
  if (Gate === undefined) return <>{children}</>
  if (session === null) return null
  return (
    <Gate slug={session.slug} sessionToken={session.sessionToken}>
      {children}
    </Gate>
  )
}
