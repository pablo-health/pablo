// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Tests for the onboarding surface contract helpers — the pure gating
 * and step-numbering logic every onboarding surface shares. The
 * concrete step list is deployment-specific (resolved by
 * getOnboardingSurface); these guard the logic that operates on it.
 */

import { describe, it, expect } from "vitest"
import type { UserStatus } from "@/lib/api/users"
import {
  firstIncompleteStep,
  firstIncompleteRequiredStep,
  stepIndex,
  requiredStepPosition,
  type OnboardingSurface,
  type StepDef,
} from "../types"

// The gates below only read fields they care about, so a partial cast is
// enough to drive them.
function status(fields: Partial<UserStatus>): UserStatus {
  return fields as UserStatus
}

function surfaceOf(steps: StepDef[]): OnboardingSurface {
  return { steps }
}

// Mirrors the stock minimal surface: a lone required passkey step.
const second = surfaceOf([
  {
    id: "passkey",
    path: "/onboarding/passkey",
    gate: (s) => Boolean(s.mfa_enrolled_at),
  },
])

describe("firstIncompleteStep", () => {
  it("returns the first step whose gate is unsatisfied", () => {
    const s = surfaceOf([
      { id: "a", path: "/a", gate: () => true },
      { id: "b", path: "/b", gate: () => false },
      { id: "c", path: "/c", gate: () => false },
    ])
    expect(firstIncompleteStep(s, status({}))?.id).toBe("b")
  })

  it("returns null when every gate is satisfied", () => {
    expect(firstIncompleteStep(second, status({ mfa_enrolled_at: "2026-07-14" }))).toBeNull()
  })

  it("returns null for an empty surface", () => {
    expect(firstIncompleteStep(surfaceOf([]), status({}))).toBeNull()
  })
})

describe("firstIncompleteRequiredStep", () => {
  it("skips optional (required:false) steps", () => {
    const s = surfaceOf([
      { id: "opt", path: "/opt", gate: () => false, required: false },
      { id: "req", path: "/req", gate: () => false },
    ])
    expect(firstIncompleteRequiredStep(s, status({}))?.id).toBe("req")
  })

  it("returns null once every required gate clears (optional may remain)", () => {
    const s = surfaceOf([
      { id: "req", path: "/req", gate: () => true },
      { id: "opt", path: "/opt", gate: () => false, required: false },
    ])
    expect(firstIncompleteRequiredStep(s, status({}))).toBeNull()
  })

  it("gates the passkey step on mfa_enrolled_at", () => {
    expect(firstIncompleteRequiredStep(second, status({ mfa_enrolled_at: null }))?.id).toBe(
      "passkey",
    )
    expect(
      firstIncompleteRequiredStep(second, status({ mfa_enrolled_at: "2026-07-14" })),
    ).toBeNull()
  })
})

describe("stepIndex", () => {
  it("returns the 0-based position, or -1 when absent", () => {
    const s = surfaceOf([
      { id: "a", path: "/a", gate: () => true },
      { id: "b", path: "/b", gate: () => true },
    ])
    expect(stepIndex(s, "b")).toBe(1)
    expect(stepIndex(s, "nope")).toBe(-1)
  })
})

describe("requiredStepPosition", () => {
  it("numbers a lone required step as 1 of 1 with no sub-label", () => {
    const pos = requiredStepPosition(second, "passkey")
    expect(pos).toEqual({ index: 1, subLabel: undefined, total: 1, fraction: 1 })
  })

  it("excludes welcome/celebration bookends and optional steps", () => {
    const s = surfaceOf([
      { id: "welcome", path: "/welcome", gate: () => true },
      { id: "one", path: "/one", gate: () => true },
      { id: "two", path: "/two", gate: () => true },
      { id: "opt", path: "/opt", gate: () => true, required: false },
      { id: "celebration", path: "/celebration", gate: () => true },
    ])
    expect(requiredStepPosition(s, "welcome")).toBeNull()
    expect(requiredStepPosition(s, "celebration")).toBeNull()
    expect(requiredStepPosition(s, "opt")).toBeNull()
    expect(requiredStepPosition(s, "one")?.index).toBe(1)
    expect(requiredStepPosition(s, "one")?.total).toBe(2)
    expect(requiredStepPosition(s, "two")?.index).toBe(2)
  })

  it("shares a base number and letters grouped steps a, b…", () => {
    const s = surfaceOf([
      { id: "solo", path: "/solo", gate: () => true },
      { id: "g1", path: "/g1", gate: () => true, group: "pair" },
      { id: "g2", path: "/g2", gate: () => true, group: "pair" },
      { id: "last", path: "/last", gate: () => true },
    ])
    // Three display slots: solo(1), pair(2a/2b), last(3).
    expect(requiredStepPosition(s, "solo")).toMatchObject({ index: 1, total: 3, subLabel: undefined })
    expect(requiredStepPosition(s, "g1")).toMatchObject({ index: 2, subLabel: "a", total: 3 })
    expect(requiredStepPosition(s, "g2")).toMatchObject({ index: 2, subLabel: "b", total: 3 })
    expect(requiredStepPosition(s, "last")).toMatchObject({ index: 3, total: 3 })
  })

  it("returns null for a step not in the surface", () => {
    expect(requiredStepPosition(second, "ghost")).toBeNull()
  })
})
