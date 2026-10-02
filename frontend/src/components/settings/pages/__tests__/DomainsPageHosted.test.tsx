// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings > Domains — the practice's hosted addresses, where the deployment
 * names a hosted domain. Each says it works only when the server says it
 * serves something, and says where it sends visitors once the practice's own
 * domain is its primary.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { DomainsPage } from "../DomainsPage"
import { renderWithProviders } from "@/test/renderWithProviders"
import type { HostedAddresses, PracticeDomain } from "@/lib/api/practiceDomains"

const mockList = vi.fn()
const mockStatus = vi.fn()

vi.mock("@/lib/api/practiceDomains", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/practiceDomains")>()),
  listPracticeDomains: (...a: unknown[]) => mockList(...a),
}))

vi.mock("@/lib/api/users", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/users")>()),
  getUserStatus: (...a: unknown[]) => mockStatus(...a),
}))

const PORTAL = "acme.portal.hosted.example"
const SITE = "acme.hosted.example"

function hosted(overrides: Partial<HostedAddresses> = {}): HostedAddresses {
  return { portal_host: PORTAL, portal_on: true, site_host: SITE, site_live: true, ...overrides }
}

function domain(host: string, overrides: Partial<PracticeDomain> = {}): PracticeDomain {
  return {
    domain: host,
    purpose: "portal",
    status: "active",
    is_primary: false,
    verified_at: null,
    created_at: "2026-09-01T00:00:00Z",
    dns_records: [],
    ...overrides,
  }
}

describe("DomainsPage hosted addresses", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStatus.mockResolvedValue({ is_practice_owner: true })
  })

  it("shows nothing of them where the deployment has no hosted domain", async () => {
    mockList.mockResolvedValue({ domains: [], hosted: null })
    renderWithProviders(<DomainsPage />)

    expect(await screen.findByText("Add a domain")).toBeVisible()
    expect(screen.queryByText("Your addresses")).not.toBeInTheDocument()
  })

  it("calls both active when the portal is on and a website is published", async () => {
    mockList.mockResolvedValue({ domains: [], hosted: hosted() })
    renderWithProviders(<DomainsPage />)

    const portal = await screen.findByTestId("hosted-address-portal")
    expect(within(portal).getByText(PORTAL)).toBeVisible()
    expect(within(portal).getByText("Active")).toBeVisible()
    const site = screen.getByTestId("hosted-address-site")
    expect(within(site).getByText(SITE)).toBeVisible()
    expect(within(site).getByText("Active")).toBeVisible()
    expect(screen.getByText(/A domain of your own can replace them/)).toBeVisible()
  })

  it("says what each waits on, and never calls it active, before there is anything to serve", async () => {
    mockList.mockResolvedValue({ domains: [], hosted: hosted({ portal_on: false, site_live: false }) })
    renderWithProviders(<DomainsPage />)

    const portal = await screen.findByTestId("hosted-address-portal")
    expect(within(portal).getByText("Works once your client portal is turned on.")).toBeVisible()
    const site = screen.getByTestId("hosted-address-site")
    expect(within(site).getByText("Works once you publish your website.")).toBeVisible()
    expect(screen.queryByText("Active")).not.toBeInTheDocument()
  })

  it("says where it sends visitors once the practice's own domain is the working primary", async () => {
    mockList.mockResolvedValue({
      domains: [
        domain("portal.example.com", { is_primary: true }),
        // Not active yet, so the hosted website address is still the address.
        domain("example.com", { purpose: "site", is_primary: true, status: "pending" }),
      ],
      hosted: hosted(),
    })
    renderWithProviders(<DomainsPage />)

    const portal = await screen.findByTestId("hosted-address-portal")
    expect(within(portal).getByText("Sends visitors to portal.example.com.")).toBeVisible()
    expect(within(portal).queryByText("Active")).not.toBeInTheDocument()
    const site = screen.getByTestId("hosted-address-site")
    expect(within(site).getByText("Active")).toBeVisible()
    expect(within(site).queryByText(/Sends visitors/)).not.toBeInTheDocument()
  })

  it("copies the address as a link", async () => {
    const user = userEvent.setup()
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue()
    mockList.mockResolvedValue({ domains: [], hosted: hosted() })
    renderWithProviders(<DomainsPage />)

    await user.click(await screen.findByRole("button", { name: "Copy client portal address" }))

    expect(writeText).toHaveBeenCalledWith(`https://${PORTAL}`)
    expect(within(screen.getByTestId("hosted-address-portal")).getByText("Copied")).toBeVisible()
  })
})
