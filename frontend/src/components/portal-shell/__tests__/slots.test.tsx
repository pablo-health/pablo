// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The portal shell's slot registry — the seam intake and messaging mount
 * through.
 */

import { beforeEach, describe, expect, it } from "vitest"
import { getPortalSlots, registerPortalSlot, resetPortalSlotsForTests } from "../slots"

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
