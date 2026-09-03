// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook } from "@testing-library/react"

const deployment = vi.hoisted(() => ({ features: {} as Record<string, boolean> }))
const account = vi.hoisted(() => ({
  value: { features: {} as Record<string, boolean>, resolved: true },
}))

vi.mock("../config", () => ({ useConfig: () => deployment }))
vi.mock("../featureGates.extensions", () => ({ useAccountFeatures: () => account.value }))

import { useFeature } from "../featureGates"

/**
 * Which optional features are on for the person looking at the screen.
 *
 * The deployment answers for everyone; a downstream build can answer per
 * account on top. The two must compose in one direction only, or a practice
 * granted an early look loses it the moment the environment changes.
 */
describe("useFeature", () => {
  beforeEach(() => {
    deployment.features = {}
    account.value = { features: {}, resolved: true }
  })

  it("allows anything that is not gated at all", () => {
    const { result } = renderHook(() => useFeature(undefined))
    expect(result.current).toBe(true)
  })

  it("is off for a feature nobody turned on", () => {
    // New features are dark until somebody enables them, rather than dark
    // until somebody remembers to hide them.
    const { result } = renderHook(() => useFeature("patient_portal"))
    expect(result.current).toBe(false)
  })

  it("is on when the deployment turned it on", () => {
    deployment.features = { patient_portal: true }
    const { result } = renderHook(() => useFeature("patient_portal"))
    expect(result.current).toBe(true)
  })

  it("lets an account answer override the deployment, both ways", () => {
    deployment.features = { patient_portal: false, superbills: true }
    account.value = { features: { patient_portal: true, superbills: false }, resolved: true }

    const { result: granted } = renderHook(() => useFeature("patient_portal"))
    const { result: revoked } = renderHook(() => useFeature("superbills"))

    expect(granted.current).toBe(true)
    expect(revoked.current).toBe(false)
  })

  it("defers to the deployment for a feature the account has no answer on", () => {
    deployment.features = { superbills: true }
    account.value = { features: { patient_portal: true }, resolved: true }

    const { result } = renderHook(() => useFeature("superbills"))
    expect(result.current).toBe(true)
  })

  it("uses the deployment answer while the account answer is still loading", () => {
    // Treating "not loaded yet" as "not for you" would blink a feature out of
    // the nav on every page load for the people who do have it.
    deployment.features = { superbills: true }
    account.value = { features: {}, resolved: false }

    const { result } = renderHook(() => useFeature("superbills"))
    expect(result.current).toBe(true)
  })
})
