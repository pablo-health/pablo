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
const mockStatus = vi.fn()

vi.mock("@/lib/api/practiceDomains", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/practiceDomains")>()),
  listPracticeDomains: (...a: unknown[]) => mockList(...a),
  addPracticeDomain: (...a: unknown[]) => mockAdd(...a),
  makePracticeDomainPrimary: (...a: unknown[]) => mockPrimary(...a),
  removePracticeDomain: (...a: unknown[]) => mockRemove(...a),
}))

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
    const www = screen.getByRole("checkbox", { name: "Also add www.example.com" })
    expect(www).toBeChecked()
    await user.click(screen.getByRole("button", { name: "Add domain" }))
    expect(mockAdd).toHaveBeenCalledWith({ domain: "example.com", purpose: "site", include_www: true })

    await user.type(screen.getByLabelText("Domain"), "example.org")
    await user.click(screen.getByRole("checkbox", { name: "Also add www.example.org" }))
    await user.click(screen.getByRole("button", { name: "Add domain" }))
    expect(mockAdd).toHaveBeenLastCalledWith({ domain: "example.org", purpose: "site", include_www: false })
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
