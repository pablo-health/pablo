// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient portal shell's mount-slot registry.
 *
 * A feature that belongs on the portal registers a component here instead
 * of editing the shell body, so the shell stays one file that two features
 * never have to edit at once. Order is registration order.
 *
 * Every slot is handed the practice slug and the live patient session
 * token: the shell already holds both, and a slot that had to rediscover
 * the session would be re-deriving state the shell just proved.
 */

import type { ComponentType } from "react"

/** What the shell hands every mounted slot. */
export interface PortalSlotProps {
  slug: string
  sessionToken: string
}

export interface PortalSlot {
  id: string
  Component: ComponentType<PortalSlotProps>
}

const slots: PortalSlot[] = []

/** Idempotent: re-registering an id already present is a no-op. */
export function registerPortalSlot(slot: PortalSlot): void {
  if (slots.some((existing) => existing.id === slot.id)) return
  slots.push(slot)
}

/** The registered slots, in registration order. A fresh array each call. */
export function getPortalSlots(): PortalSlot[] {
  return [...slots]
}

/** Test-only: clears the registry so one test's slot can't leak into another. */
export function resetPortalSlotsForTests(): void {
  slots.length = 0
}
