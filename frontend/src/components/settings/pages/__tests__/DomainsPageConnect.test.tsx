// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings > Domains — one-click setup at the practice's DNS provider.
 *
 * The button appears only where the server signed a link, and only for the
 * owner. Coming back from the provider hands the return to the server and
 * shows what its DNS check found — never that the setup is done.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, waitFor, within } from "@testing-library/react"

import { DomainsPage } from "../DomainsPage"
import { renderWithProviders } from "@/test/renderWithProviders"
import { ApiError } from "@/lib/api/client"
import type { DomainConnectList } from "@/lib/api/domainConnect"
import type { PracticeDomain } from "@/lib/api/practiceDomains"

const mockList = vi.fn()
const mockStatus = vi.fn()
const mockConnect = vi.fn()
const mockReturn = vi.fn()

vi.mock("@/lib/api/practiceDomains", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/practiceDomains")>()),
  listPracticeDomains: (...a: unknown[]) => mockList(...a),
}))

vi.mock("@/lib/api/domainConnect", () => ({
  listDomainConnect: (...a: unknown[]) => mockConnect(...a),
  returnFromDomainConnect: (...a: unknown[]) => mockReturn(...a),
}))

vi.mock("@/lib/api/users", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/users")>()),
  getUserStatus: (...a: unknown[]) => mockStatus(...a),
}))

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: { uid: "u1" }, loading: false, getIdToken: async () => "token" }),
}))

const PORTAL_URL = "https://dns.example.net/v2/domainTemplates/providers/p.example/services/practice-domain/apply?x=1"
const SITE_URL = "https://dns.example.net/v2/domainTemplates/providers/p.example/services/practice-website/apply?x=1"

function host(domain: string, overrides: Partial<PracticeDomain> = {}): PracticeDomain {
  return {
    domain,
    purpose: "portal",
    status: "pending",
    is_primary: false,
    verified_at: null,
    created_at: "2026-09-01T00:00:00Z",
    dns_records: [{ type: "CNAME", name: domain, value: "sites.example.net" }],
    ...overrides,
  }
}

const CONNECT: DomainConnectList = {
  domains: [
    {
      apex: "example.com",
      offers: [
        {
          service_id: "practice-domain",
          purpose: "portal",
          supported: true,
          provider_name: "Squarespace",
          url: PORTAL_URL,
          reason: null,
        },
        {
          service_id: "practice-website",
          purpose: "site",
          supported: null,
          provider_name: null,
          url: null,
          reason: "hosts_differ",
        },
      ],
    },
  ],
}

describe("DomainsPage one-click setup", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.history.replaceState({}, "", "/dashboard/settings/domains")
    mockStatus.mockResolvedValue({ is_practice_owner: true })
    mockList.mockResolvedValue({
      domains: [host("portal.example.com"), host("example.com", { purpose: "site" })],
    })
    mockConnect.mockResolvedValue(CONNECT)
  })

  it("offers the provider's setup only where the server signed a link", async () => {
    renderWithProviders(<DomainsPage />)

    const link = await screen.findByRole("link", { name: "Set up with Squarespace" })
    expect(link).toHaveAttribute("href", PORTAL_URL)
    expect(screen.getByTestId("domain-connect-example.com")).toHaveTextContent(
      "Squarespace can add the records for example.com for you.",
    )
    // The website offer has no link, so nothing is shown for it.
    expect(screen.getAllByRole("link", { name: /Set up with/ })).toHaveLength(1)
    const portalCard = screen.getByRole("list", { name: "Client portal domains" }).parentElement
    expect(portalCard && within(portalCard).getByRole("link", { name: "Set up with Squarespace" })).toBeTruthy()
  })

  it("puts a website offer in the website section", async () => {
    mockConnect.mockResolvedValue({
      domains: [
        {
          apex: "example.com",
          offers: [
            { service_id: "practice-website", purpose: "site", supported: true, provider_name: "Cloudflare", url: SITE_URL, reason: null },
          ],
        },
      ],
    })
    renderWithProviders(<DomainsPage />)

    const link = await screen.findByRole("link", { name: "Set up with Cloudflare" })
    expect(link).toHaveAttribute("href", SITE_URL)
    const siteCard = screen.getByRole("list", { name: "Website domains" }).parentElement
    expect(siteCard && within(siteCard).getByRole("link", { name: "Set up with Cloudflare" })).toBeTruthy()
  })

  it("asks nothing and shows nothing to someone who is not the owner", async () => {
    mockStatus.mockResolvedValue({ is_practice_owner: false })
    renderWithProviders(<DomainsPage />)

    expect(await screen.findByText("Only the practice owner can change domains.")).toBeVisible()
    expect(mockConnect).not.toHaveBeenCalled()
    expect(screen.queryByRole("link", { name: /Set up with/ })).toBeNull()
  })

  it("on return, hands the state to the server and shows what the check found", async () => {
    window.history.replaceState({}, "", "/dashboard/settings/domains?state=signed-state")
    let finish: (value: unknown) => void = () => {}
    mockReturn.mockReturnValue(new Promise((resolve) => (finish = resolve)))
    renderWithProviders(<DomainsPage />)

    expect(await screen.findByText("Checking your records…")).toBeVisible()
    expect(mockReturn).toHaveBeenCalledTimes(1)
    expect(mockReturn).toHaveBeenCalledWith({ state: "signed-state" })
    // The query is dropped so a reload does not send it again.
    expect(window.location.search).toBe("")

    finish({
      apex: "example.com",
      error: null,
      domains: [
        host("portal.example.com", {
          dns_records: [{ type: "CNAME", name: "portal.example.com", value: "sites.example.net", check: "ok", found: ["sites.example.net"] }],
        }),
      ],
    })

    expect(await screen.findByTestId("domain-connect-result")).toHaveTextContent(
      "Below is what we found in your DNS. New records can take a few minutes to show up.",
    )
    expect(screen.getByTestId("check-CNAME-portal.example.com")).toHaveTextContent("Found")
    // Coming back is not the host working.
    expect(within(screen.getByTestId("domain-row-portal.example.com")).getByText("Waiting for DNS")).toBeVisible()
    expect(screen.queryByText(/done|ready|connected/i)).toBeNull()
  })

  it("passes on the provider's error, and still shows the check", async () => {
    window.history.replaceState({}, "", "/dashboard/settings/domains?state=signed-state&error=access_denied")
    mockReturn.mockResolvedValue({ apex: "example.com", error: "access_denied", domains: [host("portal.example.com")] })
    renderWithProviders(<DomainsPage />)

    expect(await screen.findByTestId("domain-connect-result")).toHaveTextContent(
      "Your DNS provider didn't make the change. Below is what we found in your DNS.",
    )
    expect(mockReturn).toHaveBeenCalledWith({ state: "signed-state", error: "access_denied" })
  })

  it("says so when the server refuses the return", async () => {
    window.history.replaceState({}, "", "/dashboard/settings/domains?state=old-state")
    mockReturn.mockRejectedValue(
      new ApiError("DOMAIN_CONNECT_STATE", "That link has expired. Start again from this page."),
    )
    renderWithProviders(<DomainsPage />)

    expect(await screen.findByRole("alert")).toHaveTextContent("That link has expired. Start again from this page.")
  })

  it("does nothing on an ordinary visit", async () => {
    renderWithProviders(<DomainsPage />)
    await screen.findByRole("link", { name: "Set up with Squarespace" })
    await waitFor(() => expect(mockConnect).toHaveBeenCalled())
    expect(mockReturn).not.toHaveBeenCalled()
  })
})
