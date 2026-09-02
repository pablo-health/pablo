// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook } from "@testing-library/react"

const slot = vi.hoisted(() => ({
  value: { gates: {} as Record<string, boolean>, resolved: true },
}))

vi.mock("../featureGates.extensions", () => ({
  useFeatureGates: () => slot.value,
}))

import { useFeatureGate } from "../featureGates"

/**
 * The gate decides whether an unreleased settings page exists for this account.
 * A deployment turns a key on in one environment and off in another, and may
 * grant it to a single practice, so the slot has to win over the build flag.
 */
describe("useFeatureGate", () => {
  beforeEach(() => {
    slot.value = { gates: {}, resolved: true }
  })

  it("allows an ungated surface", () => {
    const { result } = renderHook(() => useFeatureGate(undefined))
    expect(result.current).toBe(true)
  })

  it("lets the deployment turn a surface on", () => {
    slot.value = { gates: { patient_portal: true }, resolved: true }
    const { result } = renderHook(() => useFeatureGate("patient_portal"))
    expect(result.current).toBe(true)
  })

  it("lets the deployment turn a surface off even when a build flag enables it", () => {
    // session_defaults is true in the static build flags.
    slot.value = { gates: { session_defaults: false }, resolved: true }
    const { result } = renderHook(() => useFeatureGate("session_defaults"))
    expect(result.current).toBe(false)
  })

  it("falls through to the build flag for a key the deployment has no opinion on", () => {
    const { result } = renderHook(() => useFeatureGate("session_defaults"))
    expect(result.current).toBe(true)
  })

  it("keeps an unknown key off, so a new gated page is dark by default", () => {
    const { result } = renderHook(() => useFeatureGate("not_a_real_key"))
    expect(result.current).toBe(false)
  })

  it("stays closed while the deployment's answer is still loading", () => {
    // Returning true here would flash every unreleased page on each page load.
    slot.value = { gates: {}, resolved: false }
    const { result } = renderHook(() => useFeatureGate("session_defaults"))
    expect(result.current).toBe(false)
  })
})
