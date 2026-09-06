// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"

const routeParams = vi.hoisted(() => ({ section: "appearance" }))
const gate = vi.hoisted(() => ({ allow: true }))

const notFound = vi.hoisted(() =>
  vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND")
  })
)

vi.mock("next/navigation", () => ({
  notFound,
  useParams: () => routeParams,
}))

vi.mock("@/lib/featureGates", () => ({
  useFeature: () => gate.allow,
  useFeaturePredicate: () => () => gate.allow,
}))

vi.mock("@/components/settings/registry", () => ({
  findSettingsItem: (id: string) =>
    ({
      appearance: { id: "appearance", page: () => <div data-testid="appearance-page" /> },
      portal: { id: "portal", feature: "patient_portal", page: () => <div data-testid="portal-page" /> },
    })[id],
}))

import SettingsSectionPage from "../page"

/**
 * A settings URL is guessable, bookmarkable and shareable, so hiding the nav
 * link is not enough — the route has to refuse on its own. Same posture as the
 * calendar setup route.
 */
describe("SettingsSectionPage", () => {
  beforeEach(() => {
    routeParams.section = "appearance"
    gate.allow = true
    notFound.mockClear()
  })

  it("renders the page for a known, allowed item", () => {
    render(<SettingsSectionPage />)

    expect(screen.getByTestId("appearance-page")).toBeInTheDocument()
    expect(notFound).not.toHaveBeenCalled()
  })

  it("404s for a section that is not in the registry", () => {
    routeParams.section = "not-a-section"

    expect(() => render(<SettingsSectionPage />)).toThrow("NEXT_NOT_FOUND")
    expect(notFound).toHaveBeenCalled()
  })

  it("404s for a gated item this account may not see, even by direct URL", () => {
    routeParams.section = "portal"
    gate.allow = false

    expect(() => render(<SettingsSectionPage />)).toThrow("NEXT_NOT_FOUND")
    expect(notFound).toHaveBeenCalled()
  })

})
