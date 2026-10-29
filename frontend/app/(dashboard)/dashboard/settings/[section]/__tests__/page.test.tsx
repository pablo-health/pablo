// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"

const routeParams = vi.hoisted(() => ({ section: "appearance" }))
const gate = vi.hoisted(() => ({ allow: true as boolean | null }))

const notFound = vi.hoisted(() =>
  vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND")
  })
)

const redirect = vi.hoisted(() =>
  vi.fn((href: string) => {
    throw new Error(`NEXT_REDIRECT ${href}`)
  })
)
const search = vi.hoisted(() => ({ current: new URLSearchParams() }))

vi.mock("next/navigation", () => ({
  notFound,
  redirect,
  useParams: () => routeParams,
  useSearchParams: () => search.current,
}))

vi.mock("@/lib/featureGates", () => ({
  useFeature: () => gate.allow === true,
  useSettledFeature: (name: string | undefined) => (name ? gate.allow : true),
  useFeaturePredicate: () => () => gate.allow === true,
}))

vi.mock("@/components/settings/registry", () => ({
  findSettingsItem: (id: string) =>
    ({
      appearance: { id: "appearance", page: () => <div data-testid="appearance-page" /> },
      portal: { id: "portal", feature: "patient_portal", page: () => <div data-testid="portal-page" /> },
    })[id],
  movedSettingsHref: (section: string, params: URLSearchParams) =>
    section === "compliance" ? `/dashboard/settings/tasks?${params.toString()}` : null,
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
    search.current = new URLSearchParams()
    notFound.mockClear()
    redirect.mockClear()
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

  it("waits for the account's answer before refusing a gated item", () => {
    // A direct load of a page this account was given: the deployment says no
    // until the per-account answer lands, and a 404 there is final.
    routeParams.section = "portal"
    gate.allow = null

    const { container } = render(<SettingsSectionPage />)

    expect(notFound).not.toHaveBeenCalled()
    expect(container).toBeEmptyDOMElement()
  })

  it("sends a section that moved on to its new address, query and all", () => {
    routeParams.section = "compliance"
    search.current = new URLSearchParams("item=abc")

    expect(() => render(<SettingsSectionPage />)).toThrow(
      "NEXT_REDIRECT /dashboard/settings/tasks?item=abc",
    )
    expect(redirect).toHaveBeenCalled()
    expect(notFound).not.toHaveBeenCalled()
  })
})
