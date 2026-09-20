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
  /**
   * The portal module this slot belongs to, if any.
   *
   * The shell renders a slot only when the deployment's capability document
   * says its module is on. A slot with no `module` always renders: it is
   * not part of a module and has nothing to be gated by.
   *
   * This is presentation, not authorization, and the difference matters. A
   * module the deployment did not name has its routes left unmounted, so a
   * slot that somehow rendered anyway would meet a 404 rather than reach
   * anything. What this prevents is a patient being shown a section of a
   * portal their practice does not have.
   */
  module?: string
  /** What the navigation calls this slot. Omitted means no nav entry. */
  label?: string
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

/**
 * The registered slots this deployment actually serves.
 *
 * `modules` is the capability document's module map, or `null` when it
 * could not be fetched. A slot whose module is off is dropped; one with no
 * module is always kept.
 *
 * **`null` keeps everything**, which is the right way round. The capability
 * document is a courtesy that tells the shell what to draw, not a gate:
 * every module's routes are unmounted when the deployment did not name
 * them, so a slot rendered for a module that is off shows its own error and
 * nothing more. Hiding the whole portal because one fetch failed would
 * break a working session over a cosmetic call.
 */
export function visiblePortalSlots(modules: Record<string, boolean> | null): PortalSlot[] {
  if (modules === null) return getPortalSlots()
  return getPortalSlots().filter((slot) => slot.module === undefined || modules[slot.module])
}

/** Test-only: clears the registry so one test's slot can't leak into another. */
export function resetPortalSlotsForTests(): void {
  slots.length = 0
}
