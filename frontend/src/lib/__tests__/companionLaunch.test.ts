// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { takeOpenPromptHint } from "../companionLaunch"

const WIN_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 Safari/537.36"
const WIN_FIREFOX = "Mozilla/5.0 (Windows NT 10.0; rv:130.0) Gecko/20100101 Firefox/130.0"
const MAC_CHROME = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/130 Safari/537.36"

beforeEach(() => window.localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe("takeOpenPromptHint", () => {
  it("shows once on Chrome for Windows, then never again", () => {
    vi.stubGlobal("navigator", { userAgent: WIN_CHROME })
    expect(takeOpenPromptHint()).toBe(true)
    expect(takeOpenPromptHint()).toBe(false)
  })

  it("does not show on other browsers or platforms", () => {
    vi.stubGlobal("navigator", { userAgent: WIN_FIREFOX })
    expect(takeOpenPromptHint()).toBe(false)
    vi.stubGlobal("navigator", { userAgent: MAC_CHROME })
    expect(takeOpenPromptHint()).toBe(false)
  })
})
