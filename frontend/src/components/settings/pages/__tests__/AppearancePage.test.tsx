// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"

vi.mock("../../useSettingsPreferences", () => ({
  useSettingsPreferences: () => ({
    preferences: { theme: "warm-paper", calendar_density: "balanced" },
    save: vi.fn(),
    isSaving: false,
  }),
}))

vi.mock("@/components/theme/ThemeSwitcher", () => ({
  ThemeSwitcher: () => <div data-testid="theme-switcher" />,
}))
vi.mock("@/components/theme/ThemeFlavorNote", () => ({
  ThemeFlavorNote: () => <div data-testid="theme-flavor-note" />,
}))
vi.mock("../../CalendarDensitySettings", () => ({
  CalendarDensitySettings: () => <div data-testid="calendar-density-settings" />,
}))

import { AppearancePage } from "../AppearancePage"

/**
 * Theme and density were split across a forked page once and one build lost
 * density entirely. Asserting they arrive together is the cheap guard.
 */
describe("AppearancePage", () => {
  it("offers the theme switcher and the density control together", () => {
    render(<AppearancePage />)

    expect(screen.getByTestId("theme-switcher")).toBeInTheDocument()
    expect(screen.getByTestId("theme-flavor-note")).toBeInTheDocument()
    expect(screen.getByTestId("calendar-density-settings")).toBeInTheDocument()
  })
})
