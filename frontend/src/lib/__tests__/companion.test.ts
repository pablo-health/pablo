// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, describe, expect, it, vi } from "vitest"
import { companionPlatform, isCompanionAvailable, isWindows } from "../companion"

const flags = vi.hoisted(() => ({ companion_mac: true, companion_windows: true }))

vi.mock("../featureFlags", () => ({
  isEnabled: (flag: "companion_mac" | "companion_windows") => flags[flag],
}))

function setPlatform(platform: string, userAgent: string) {
  vi.stubGlobal("navigator", { platform, userAgent })
}

const MAC = ["MacIntel", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/130"] as const
const WIN = ["Win32", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130"] as const
const LINUX = ["Linux x86_64", "Mozilla/5.0 (X11; Linux x86_64) Chrome/130"] as const

afterEach(() => {
  vi.unstubAllGlobals()
  flags.companion_mac = true
  flags.companion_windows = true
})

describe("companion platform helpers", () => {
  it("detects Windows and not macOS or Linux", () => {
    setPlatform(...WIN)
    expect(isWindows()).toBe(true)
    expect(companionPlatform()).toBe("windows")
    setPlatform(...MAC)
    expect(isWindows()).toBe(false)
    expect(companionPlatform()).toBe("macos")
    setPlatform(...LINUX)
    expect(companionPlatform()).toBeNull()
  })

  it("is available on Windows only when companion_windows is on", () => {
    setPlatform(...WIN)
    expect(isCompanionAvailable()).toBe(true)
    flags.companion_windows = false
    expect(isCompanionAvailable()).toBe(false)
  })

  it("keeps macOS gated on companion_mac alone", () => {
    setPlatform(...MAC)
    flags.companion_windows = false
    expect(isCompanionAvailable()).toBe(true)
    flags.companion_mac = false
    expect(isCompanionAvailable()).toBe(false)
  })

  it("is never available on Linux", () => {
    setPlatform(...LINUX)
    expect(isCompanionAvailable()).toBe(false)
  })
})
