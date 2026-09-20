// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The portal shell's slot registry — the seam intake and messaging mount
 * through.
 */

import { beforeEach, describe, expect, it } from "vitest"
import {
  getPortalSlots,
  registerPortalSlot,
  resetPortalSlotsForTests,
  visiblePortalSlots,
} from "../slots"

beforeEach(() => {
  resetPortalSlotsForTests()
})

describe("portal slot registry", () => {
  it("starts empty", () => {
    expect(getPortalSlots()).toEqual([])
  })

  it("returns registered slots in registration order", () => {
    const First = () => null
    const Second = () => null
    registerPortalSlot({ id: "first", Component: First })
    registerPortalSlot({ id: "second", Component: Second })

    const slots = getPortalSlots()
    expect(slots.map((s) => s.id)).toEqual(["first", "second"])
    expect(slots[0].Component).toBe(First)
    expect(slots[1].Component).toBe(Second)
  })

  it("is idempotent for a repeated id", () => {
    const Original = () => null
    const Replacement = () => null
    registerPortalSlot({ id: "intake", Component: Original })
    registerPortalSlot({ id: "intake", Component: Replacement })

    const slots = getPortalSlots()
    expect(slots).toHaveLength(1)
    expect(slots[0].Component).toBe(Original)
  })

  it("returns a fresh array each call, not the live registry", () => {
    registerPortalSlot({ id: "one", Component: () => null })
    const first = getPortalSlots()
    first.pop()

    expect(getPortalSlots()).toHaveLength(1)
  })
})

describe("gating slots on what the deployment serves", () => {
  beforeEach(() => {
    registerPortalSlot({ id: "intake", module: "intake", Component: () => null })
    registerPortalSlot({ id: "messaging", module: "messaging", Component: () => null })
    registerPortalSlot({ id: "notice", Component: () => null })
  })

  it("keeps a slot whose module is on", () => {
    const visible = visiblePortalSlots({ intake: true, messaging: false })

    expect(visible.map((s) => s.id)).toEqual(["intake", "notice"])
  })

  it("drops a slot whose module is off", () => {
    const visible = visiblePortalSlots({ intake: false, messaging: false })

    expect(visible.map((s) => s.id)).toEqual(["notice"])
  })

  it("drops a slot whose module the document does not mention", () => {
    /**
     * An absent key reads as off, which is the safe direction: a module
     * this deployment has never heard of has no routes mounted either.
     */
    const visible = visiblePortalSlots({})

    expect(visible.map((s) => s.id)).toEqual(["notice"])
  })

  it("keeps a slot with no module whatever the document says", () => {
    expect(visiblePortalSlots({}).map((s) => s.id)).toContain("notice")
  })

  it("keeps everything when there is no document", () => {
    /**
     * The failure direction. Every module's routes are unmounted when the
     * deployment did not name them, so a slot drawn for a module that is
     * off shows its own error — whereas hiding a working portal over one
     * failed fetch takes the whole thing down.
     */
    const visible = visiblePortalSlots(null)

    expect(visible.map((s) => s.id)).toEqual(["intake", "messaging", "notice"])
  })

  it("preserves registration order", () => {
    const visible = visiblePortalSlots({ intake: true, messaging: true })

    expect(visible.map((s) => s.id)).toEqual(["intake", "messaging", "notice"])
  })
})
