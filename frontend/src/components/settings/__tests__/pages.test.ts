// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect } from "vitest"
import { settingsItems } from "../registry"
import { settingsPages } from "../pages"

/**
 * The registry and the page map are edited in different files, so they drift.
 * A nav item with no page ships as a 404 the user finds; a page with no item is
 * unreachable code. Both are cheap to catch here and expensive to catch later.
 */
describe("settings registry and page map agree", () => {
  it("gives every registry item a page to render", () => {
    const missing = settingsItems.filter((item) => !settingsPages[item.id]).map((item) => item.id)

    expect(missing).toEqual([])
  })

  it("has no page that no registry item points at", () => {
    const ids = new Set(settingsItems.map((item) => item.id))
    const orphans = Object.keys(settingsPages).filter((id) => !ids.has(id))

    expect(orphans).toEqual([])
  })
})
