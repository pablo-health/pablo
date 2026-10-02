// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings > Domains — what the practice sees for each status, what the owner
 * can do, and what everyone else cannot.
 *
 * The screen must never say a domain works unless the server says it is
 * active, so each status is pinned to its words here.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { DomainsPage } from "../DomainsPage"
import { renderWithProviders } from "@/test/renderWithProviders"
import { ApiError } from "@/lib/api/client"
import type { PracticeDomain } from "@/lib/api/practiceDomains"

const mockList = vi.fn()
const mockAdd = vi.fn()
const mockPrimary = vi.fn()
const mockRemove = vi.fn()
const mockCheck = vi.fn()
const mockDescribe = vi.fn()
const mockStatus = vi.fn()

vi.mock("@/lib/api/practiceDomains", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/practiceDomains")>()),
  listPracticeDomains: (...a: unknown[]) => mockList(...a),
  addPracticeDomain: (...a: unknown[]) => mockAdd(...a),
  makePracticeDomainPrimary: (...a: unknown[]) => mockPrimary(...a),
  removePracticeDomain: (...a: unknown[]) => mockRemove(...a),
  checkPracticeDomains: (...a: unknown[]) => mockCheck(...a),
  describePracticeDomain: (...a: unknown[]) => mockDescribe(...a),
}))

/** The www box waits for the server's answer after a pause in typing. */
const WWW_ANSWER_MS = 3000

/** The server's reading of a name: bare when it is the registrable domain. */
const REGISTRABLE = new Set(["example.com", "example.org", "example.co.uk"])
function describeLikeTheServer(domain: string) {
  const apex = REGISTRABLE.has(domain) ? domain : domain.split(".").slice(1).join(".")
  return Promise.resolve({ domain, apex, bare: domain === apex })
}

vi.mock("@/lib/api/users", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/users")>()),
  getUserStatus: (...a: unknown[]) => mockStatus(...a),
}))

const RECORD = { type: "CNAME", name: "", value: "sites.example.net" }

function domain(host: string, overrides: Partial<PracticeDomain> = {}): PracticeDomain {
  return {
    domain: host,
    purpose: "portal",
    status: "active",
    is_primary: false,
    verified_at: null,
    created_at: "2026-09-01T00:00:00Z",
    dns_records: [{ ...RECORD, name: host }],
    ...overrides,
  }
}

function row(host: string): HTMLElement {
  return screen.getByTestId(`domain-row-${host}`)
}

describe("DomainsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStatus.mockResolvedValue({ is_practice_owner: true })
    mockDescribe.mockImplementation(describeLikeTheServer)
  })

  it("names each status, and calls only an active domain active", async () => {
    mockList.mockResolvedValue({
      domains: [
        domain("pending.example.com", { status: "pending" }),
        domain("verifying.example.com", { status: "verifying" }),
        domain("active.example.com", { status: "active", is_primary: true }),
        domain("error.example.com", { status: "error" }),
      ],
    })
    renderWithProviders(<DomainsPage />)

    expect(await within(await screen.findByTestId("domain-row-pending.example.com")).findByText("Waiting for DNS")).toBeVisible()
    expect(within(row("verifying.example.com")).getByText("Checking")).toBeVisible()
    expect(within(row("error.example.com")).getByText("Not working")).toBeVisible()
    expect(within(row("error.example.com")).getByText(/couldn't be confirmed/)).toBeVisible()

    const active = row("active.example.com")
    expect(within(active).getByText("Active")).toBeVisible()
    expect(within(active).getByText("Primary")).toBeVisible()
    // A working domain needs no DNS instructions.
    expect(within(active).queryByText(/DNS provider/)).not.toBeInTheDocument()
    expect(screen.getAllByText("Active")).toHaveLength(1)
  })

  it("tells the practice who can help with a host that is taking too long, in place of Checking", async () => {
    const message = "This is taking longer than usual. Ask the front desk."
    mockList.mockResolvedValue({
      domains: [
        domain("stuck.example.com", { status: "verifying", stuck: true, stuck_message: message }),
        domain("waiting.example.com", { status: "pending", stuck: false, stuck_message: null }),
      ],
    })
    renderWithProviders(<DomainsPage />)

    const stuck = await screen.findByTestId("domain-row-stuck.example.com")
    expect(within(stuck).getByRole("status")).toHaveTextContent(message)
    expect(within(stuck).getByText("Delayed")).toBeVisible()
    expect(within(stuck).queryByText("Checking")).not.toBeInTheDocument()

    const waiting = row("waiting.example.com")
    expect(within(waiting).getByText("Waiting for DNS")).toBeVisible()
    expect(within(waiting).queryByRole("status")).not.toBeInTheDocument()
    expect(screen.getAllByText(message)).toHaveLength(1)
  })

  it("says a stuck host's records couldn't be confirmed only when they weren't", async () => {
    const message = "This is taking longer than usual."
    mockList.mockResolvedValue({
      domains: [domain("lapsed.example.com", { status: "error", stuck: true, stuck_message: message })],
    })
    renderWithProviders(<DomainsPage />)

    const lapsed = await screen.findByTestId("domain-row-lapsed.example.com")
    expect(within(lapsed).getByRole("status")).toHaveTextContent(message)
    expect(within(lapsed).queryByText(/couldn't be confirmed/)).not.toBeInTheDocument()
  })

  it("shows the record to add for a domain that is not active yet", async () => {
    mockList.mockResolvedValue({ domains: [domain("portal.example.com", { status: "pending" })] })
    renderWithProviders(<DomainsPage />)

    const table = await screen.findByRole("table", { name: "DNS records for portal.example.com" })
    expect(within(table).getByText("CNAME")).toBeVisible()
    expect(within(table).getByText("sites.example.net")).toBeVisible()
  })

  it("shows a bare domain its address record, and the ALIAS alternative", async () => {
    mockList.mockResolvedValue({
      domains: [
        domain("example.com", {
          purpose: "site",
          status: "pending",
          dns_records: [{ type: "A", name: "example.com", value: "203.0.113.7" }],
          alias_alternative: "sites.example.net",
        }),
      ],
    })
    renderWithProviders(<DomainsPage />)

    const table = await screen.findByRole("table", { name: "DNS records for example.com" })
    expect(within(table).getByText("A")).toBeVisible()
    expect(within(table).getByText("203.0.113.7")).toBeVisible()
    expect(screen.getByTestId("alias-alternative")).toHaveTextContent(
      "If your DNS provider offers ALIAS or ANAME records, one pointing at sites.example.net works instead.",
    )
  })

  it("says where to point a domain when the server names no target", async () => {
    mockList.mockResolvedValue({
      domains: [domain("portal.example.com", { status: "pending", dns_records: [] })],
    })
    renderWithProviders(<DomainsPage />)

    expect(
      await screen.findByText("Point portal.example.com at this server in your DNS settings."),
    ).toBeVisible()
  })

  it("offers Make primary only on an active domain that is not already primary", async () => {
    mockList.mockResolvedValue({
      domains: [
        domain("new.example.com"),
        domain("old.example.com", { is_primary: true }),
        domain("pending.example.com", { status: "pending" }),
      ],
    })
    mockPrimary.mockResolvedValue({ domains: [] })
    const user = userEvent.setup()
    renderWithProviders(<DomainsPage />)

    await user.click(await within(await screen.findByTestId("domain-row-new.example.com")).findByRole("button", { name: "Make primary" }))
    expect(mockPrimary).toHaveBeenCalledWith("new.example.com")
    expect(within(row("old.example.com")).queryByRole("button", { name: "Make primary" })).toBeNull()
    expect(within(row("pending.example.com")).queryByRole("button", { name: "Make primary" })).toBeNull()
  })

  it("asks before removing", async () => {
    mockList.mockResolvedValue({ domains: [domain("portal.example.com")] })
    mockRemove.mockResolvedValue({ domains: [] })
    const user = userEvent.setup()
    renderWithProviders(<DomainsPage />)

    const target = await screen.findByTestId("domain-row-portal.example.com")
    await user.click(within(target).getByRole("button", { name: "Remove" }))
    expect(mockRemove).not.toHaveBeenCalled()
    expect(within(target).getByText("Remove portal.example.com?")).toBeVisible()

    await user.click(within(target).getByRole("button", { name: "Remove" }))
    expect(mockRemove).toHaveBeenCalledWith("portal.example.com")
  })

  it("adds a website with its www alias by default, and without it when unticked", async () => {
    mockList.mockResolvedValue({ domains: [] })
    mockAdd.mockResolvedValue({ domains: [] })
    const user = userEvent.setup()
    renderWithProviders(<DomainsPage />)

    const input = await screen.findByLabelText("Domain")
    await user.type(input, "example.com")
    await user.click(screen.getByRole("radio", { name: "Website" }))
    const www = await screen.findByRole("checkbox", { name: "Also add www.example.com" }, { timeout: WWW_ANSWER_MS })
    expect(www).toBeChecked()
    await user.click(screen.getByRole("button", { name: "Add domain" }))
    // Untouched, the box showed the server's default, so the server applies it.
    expect(mockAdd).toHaveBeenCalledWith({ domain: "example.com", purpose: "site" })

    await user.type(screen.getByLabelText("Domain"), "example.org")
    await user.click(await screen.findByRole("checkbox", { name: "Also add www.example.org" }, { timeout: WWW_ANSWER_MS }))
    await user.click(screen.getByRole("button", { name: "Add domain" }))
    expect(mockAdd).toHaveBeenLastCalledWith({ domain: "example.org", purpose: "site", include_www: false })
  })

  it("shows the server's www default for a bare domain under a two-part suffix, and leaves it to the server", async () => {
    mockList.mockResolvedValue({ domains: [] })
    mockAdd.mockResolvedValue({ domains: [] })
    const user = userEvent.setup()
    renderWithProviders(<DomainsPage />)

    await user.click(await screen.findByRole("radio", { name: "Website" }))
    await user.type(screen.getByLabelText("Domain"), "example.co.uk")
    // Three labels, but the server reads it as a bare domain: ticked.
    expect(await screen.findByRole("checkbox", { name: "Also add www.example.co.uk" }, { timeout: WWW_ANSWER_MS })).toBeChecked()
    expect(mockDescribe).toHaveBeenLastCalledWith("example.co.uk")

    await user.click(screen.getByRole("button", { name: "Add domain" }))
    expect(mockAdd).toHaveBeenCalledWith({ domain: "example.co.uk", purpose: "site" })
    expect(mockAdd.mock.calls[0][0]).not.toHaveProperty("include_www")
  })

  it("leaves a name under a domain unticked, and sends a tick the reader adds", async () => {
    mockList.mockResolvedValue({ domains: [] })
    mockAdd.mockResolvedValue({ domains: [] })
    const user = userEvent.setup()
    renderWithProviders(<DomainsPage />)

    await user.click(await screen.findByRole("radio", { name: "Website" }))
    await user.type(screen.getByLabelText("Domain"), "clinic.example.co.uk")
    const www = await screen.findByRole("checkbox", { name: "Also add www.clinic.example.co.uk" }, { timeout: WWW_ANSWER_MS })
    expect(www).not.toBeChecked()
    await user.click(www)
    await user.click(screen.getByRole("button", { name: "Add domain" }))
    expect(mockAdd).toHaveBeenCalledWith({ domain: "clinic.example.co.uk", purpose: "site", include_www: true })
  })

  it("adds a portal domain with no www question", async () => {
    mockList.mockResolvedValue({ domains: [] })
    mockAdd.mockResolvedValue({ domains: [] })
    const user = userEvent.setup()
    renderWithProviders(<DomainsPage />)

    await user.type(await screen.findByLabelText("Domain"), "example.com")
    expect(screen.queryByRole("checkbox")).toBeNull()
    await user.click(screen.getByRole("button", { name: "Add domain" }))
    expect(mockAdd).toHaveBeenCalledWith({ domain: "example.com", purpose: "portal" })
  })

  it("shows the server's reason when a domain can't be added", async () => {
    mockList.mockResolvedValue({ domains: [] })
    mockAdd.mockRejectedValue(new ApiError("DOMAIN_TAKEN", "That domain is already in use.", {}, 409))
    const user = userEvent.setup()
    renderWithProviders(<DomainsPage />)

    await user.type(await screen.findByLabelText("Domain"), "taken.example.com")
    await user.click(screen.getByRole("button", { name: "Add domain" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("That domain is already in use.")
  })

  it("shows the deployment's words when a domain is past the practice's allowance", async () => {
    mockList.mockResolvedValue({ domains: [] })
    mockAdd.mockRejectedValue(
      new ApiError("DOMAIN_LIMIT", "Message supplied by the deployment.", { domain: "another.example" }, 403),
    )
    const user = userEvent.setup()
    renderWithProviders(<DomainsPage />)

    await user.type(await screen.findByLabelText("Domain"), "another.example")
    await user.click(screen.getByRole("button", { name: "Add domain" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Message supplied by the deployment.")
  })

  it("shows the domain's ownership record beside the host's own", async () => {
    mockList.mockResolvedValue({
      domains: [
        domain("portal.example.com", {
          status: "pending",
          dns_records: [
            { ...RECORD, name: "portal.example.com" },
            { type: "TXT", name: "_pablo-verify.example.com", value: "pablo-verify=test-token" },
          ],
        }),
      ],
    })
    renderWithProviders(<DomainsPage />)

    const table = await screen.findByRole("table", { name: "DNS records for portal.example.com" })
    expect(screen.getByText("Add these records at your DNS provider:")).toBeVisible()
    expect(within(table).getByText("TXT")).toBeVisible()
    expect(within(table).getByText("_pablo-verify.example.com")).toBeVisible()
    expect(within(table).getByText("pablo-verify=test-token")).toBeVisible()
    // No check has run, so nothing claims a record was found.
    expect(within(table).queryByText("Check")).toBeNull()
  })

  it("keeps an active host's other records on screen, but not the one that already works", async () => {
    mockList.mockResolvedValue({
      domains: [
        domain("portal.example.com", {
          dns_records: [
            { ...RECORD, name: "portal.example.com" },
            { type: "TXT", name: "_pablo-verify.example.com", value: "pablo-verify=test-token" },
          ],
        }),
      ],
    })
    renderWithProviders(<DomainsPage />)

    const table = await screen.findByRole("table", { name: "DNS records for portal.example.com" })
    expect(within(table).getByText("TXT")).toBeVisible()
    expect(within(table).queryByText("CNAME")).toBeNull()
  })

  it("checks now and shows what was found for each record", async () => {
    const pending = domain("portal.example.com", {
      status: "pending",
      dns_records: [
        { ...RECORD, name: "portal.example.com" },
        { type: "TXT", name: "_pablo-verify.example.com", value: "pablo-verify=test-token" },
        {
          type: "CNAME",
          name: "_acme-challenge.portal.example.com",
          value: "test-auth.7.authorize.certificatemanager.goog",
        },
        { type: "CNAME", name: "k1._domainkey.example.com", value: "k1.dkim.example.net" },
      ],
    })
    mockList.mockResolvedValue({ domains: [pending] })
    mockCheck.mockResolvedValue({
      domains: [
        {
          ...pending,
          dns_records: [
            { ...pending.dns_records[0], check: "ok", found: ["sites.example.net"] },
            { ...pending.dns_records[1], check: "missing", found: [] },
            { ...pending.dns_records[2], check: "wrong", found: ["elsewhere.example.org"] },
            { ...pending.dns_records[3], check: "unknown", found: null },
          ],
        },
      ],
    })
    const user = userEvent.setup()
    renderWithProviders(<DomainsPage />)

    await user.click(await screen.findByRole("button", { name: "Check now" }))

    expect(mockCheck).toHaveBeenCalledTimes(1)
    expect(await screen.findByTestId("check-CNAME-portal.example.com")).toHaveTextContent("Found")
    expect(screen.getByTestId("check-TXT-_pablo-verify.example.com")).toHaveTextContent("Not found yet")
    expect(screen.getByTestId("check-CNAME-_acme-challenge.portal.example.com")).toHaveTextContent(
      "Doesn't matchelsewhere.example.org",
    )
    expect(screen.getByTestId("check-CNAME-k1._domainkey.example.com")).toHaveTextContent("Couldn't check")
    // A check reports records; it never moves the domain's own status.
    expect(within(row("portal.example.com")).getByText("Waiting for DNS")).toBeVisible()
    expect(screen.queryByText("Active")).toBeNull()
  })

  it("says so when the check can't be run", async () => {
    mockList.mockResolvedValue({ domains: [domain("portal.example.com", { status: "pending" })] })
    mockCheck.mockRejectedValue(new Error("network"))
    const user = userEvent.setup()
    renderWithProviders(<DomainsPage />)

    await user.click(await screen.findByRole("button", { name: "Check now" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Your records couldn't be checked. Try again.")
  })

  it("is read-only for anyone but the owner", async () => {
    mockStatus.mockResolvedValue({ is_practice_owner: false })
    mockList.mockResolvedValue({ domains: [domain("portal.example.com")] })
    renderWithProviders(<DomainsPage />)

    expect(await screen.findByText("Only the practice owner can change domains.")).toBeVisible()
    expect(screen.getByTestId("domain-row-portal.example.com")).toBeVisible()
    expect(screen.queryByRole("button")).toBeNull()
    expect(screen.queryByLabelText("Domain")).toBeNull()
  })
})
