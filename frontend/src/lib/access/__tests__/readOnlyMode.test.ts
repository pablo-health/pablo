// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * useReadOnlyMode tests
 *
 * The base implementation is a single env read, and the default matters more
 * than the flag: an unset (or misspelled, or truthy-looking-but-not-"true")
 * value must leave the deployment writable, because every call site hides an
 * affordance on a `true`.
 */

import { describe, it, expect, afterEach, vi } from "vitest"
import { renderHook } from "@testing-library/react"
import { useReadOnlyMode } from "../readOnlyMode"

afterEach(() => {
  vi.unstubAllEnvs()
})

describe("useReadOnlyMode", () => {
  it("defaults to writable when NEXT_PUBLIC_READ_ONLY is unset", () => {
    vi.stubEnv("NEXT_PUBLIC_READ_ONLY", undefined)
    const { result } = renderHook(() => useReadOnlyMode())
    expect(result.current.readOnly).toBe(false)
  })

  it("reads read-only when NEXT_PUBLIC_READ_ONLY is exactly \"true\"", () => {
    vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")
    const { result } = renderHook(() => useReadOnlyMode())
    expect(result.current.readOnly).toBe(true)
  })

  // A deployment that means to freeze writes sets "true". Everything else —
  // including values that read as truthy in JS — leaves the app writable
  // rather than half-hiding the UI on a typo.
  it.each(["false", "TRUE", "True", "1", "yes", ""])(
    "stays writable for %o",
    (value) => {
      vi.stubEnv("NEXT_PUBLIC_READ_ONLY", value)
      const { result } = renderHook(() => useReadOnlyMode())
      expect(result.current.readOnly).toBe(false)
    },
  )
})
