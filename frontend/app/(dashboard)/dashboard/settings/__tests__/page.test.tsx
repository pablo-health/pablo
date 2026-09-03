// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"

const redirect = vi.hoisted(() => vi.fn())

vi.mock("next/navigation", () => ({ redirect }))

import SettingsIndexPage from "../page"

/**
 * Settings has no landing page of its own. Sending people to the first item
 * rather than a chooser keeps every existing /dashboard/settings link working.
 */
describe("SettingsIndexPage", () => {
  it("opens settings on the first item", () => {
    SettingsIndexPage()

    expect(redirect).toHaveBeenCalledWith("/dashboard/settings/profile")
  })
})
