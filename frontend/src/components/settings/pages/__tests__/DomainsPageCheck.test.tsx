// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings > Domains — the records table as a DNS provider's form wants it,
 * and what Check now shows: in flight, after, the rest before the next click,
 * and the polling that lets a finishing host turn Active on its own.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { DomainsPage } from "../DomainsPage"
import { renderWithProviders } from "@/test/renderWithProviders"
import type { PracticeDomain } from "@/lib/api/practiceDomains"

const mockList = vi.fn()
const mockCheck = vi.fn()
const mockStatus = vi.fn()
const mockWriteText = vi.fn()

vi.mock("@/lib/api/practiceDomains", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/practiceDomains")>()),
  listPracticeDomains: (...a: unknown[]) => mockList(...a),
  checkPracticeDomains: (...a: unknown[]) => mockCheck(...a),
}))

const mockPortalSettings = vi.fn()
vi.mock("@/lib/api/portalSettings", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/portalSettings")>()),
  getPortalSettings: (...a: unknown[]) => mockPortalSettings(...a),
}))

vi.mock("@/lib/api/users", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/users")>()),
  getUserStatus: (...a: unknown[]) => mockStatus(...a),
}))

const CNAME = { type: "CNAME", name: "portal.example.com", value: "sites.example.net" }
const TXT = { type: "TXT", name: "_pablo-verify.example.com", value: "pablo-verify=test-token" }
const ACME = {
  type: "CNAME",
  name: "_acme-challenge.portal.example.com",
  value: "test-auth.7.authorize.example.net",
}

function host(overrides: Partial<PracticeDomain> = {}): PracticeDomain {
  return {
    domain: "portal.example.com",
    purpose: "portal",
    status: "pending",
    is_primary: false,
    verified_at: null,
    created_at: "2026-09-01T00:00:00Z",
    dns_records: [CNAME, TXT, ACME],
    apex: "example.com",
    ...overrides,
  }
}

/** The check's answer: every record found. */
function allFound(overrides: Partial<PracticeDomain> = {}): PracticeDomain {
  const base = host(overrides)
  return { ...base, dns_records: base.dns_records.map((r) => ({ ...r, check: "ok" as const, found: [r.value] })) }
}

function row(): HTMLElement {
  return screen.getByTestId("domain-row-portal.example.com")
}

describe("DomainsPage records table", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStatus.mockResolvedValue({ is_practice_owner: true })
    mockWriteText.mockResolvedValue(undefined)
  })

  /** After userEvent.setup(), which puts a clipboard stub of its own in place. */
  function stubClipboard(value: unknown) {
    Object.defineProperty(navigator, "clipboard", { value, writable: true, configurable: true })
  }

  it("shows each Host relative to the domain, keeping the full name for a tooltip", async () => {
    mockList.mockResolvedValue({
      domains: [
        host({ dns_records: [...host().dns_records] }),
        host({
          domain: "example.co.uk",
          purpose: "site",
          apex: "example.co.uk",
          dns_records: [{ type: "A", name: "example.co.uk", value: "203.0.113.7" }],
        }),
      ],
    })
    renderWithProviders(<DomainsPage />)

    const table = await screen.findByRole("table", { name: "DNS records for portal.example.com" })
    expect(within(table).getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
      "Type",
      "Host",
      "Value",
    ])
    expect(within(table).getByText("portal")).toHaveAttribute("title", "portal.example.com")
    expect(within(table).getByText("_pablo-verify")).toHaveAttribute("title", "_pablo-verify.example.com")
    expect(within(table).getByText("_acme-challenge.portal")).toBeVisible()

    const bare = screen.getByRole("table", { name: "DNS records for example.co.uk" })
    expect(within(bare).getByText("@")).toHaveAttribute("title", "example.co.uk")
    expect(screen.getByText(/Add this at your DNS provider\. Some providers call Host “Name”\./)).toBeVisible()
  })

  it("copies a Host and a Value, and says Copied for a moment", async () => {
    mockList.mockResolvedValue({ domains: [host()] })
    const user = userEvent.setup()
    stubClipboard({ writeText: mockWriteText })
    renderWithProviders(<DomainsPage />)

    await user.click(await screen.findByRole("button", { name: "Copy host for TXT record _pablo-verify" }))
    expect(mockWriteText).toHaveBeenLastCalledWith("_pablo-verify")
    expect(await within(row()).findByText("Copied")).toBeVisible()

    await user.click(screen.getByRole("button", { name: "Copy value for TXT record _pablo-verify" }))
    expect(mockWriteText).toHaveBeenLastCalledWith("pablo-verify=test-token")

    await user.click(screen.getByRole("button", { name: "Copy host for CNAME record _acme-challenge.portal" }))
    expect(mockWriteText).toHaveBeenLastCalledWith("_acme-challenge.portal")
  })

  it("selects the text to copy by hand when the clipboard can't be written", async () => {
    mockList.mockResolvedValue({ domains: [host()] })
    const user = userEvent.setup()
    stubClipboard(undefined)
    renderWithProviders(<DomainsPage />)

    await user.click(await screen.findByRole("button", { name: "Copy value for TXT record _pablo-verify" }))
    expect(window.getSelection()?.toString()).toBe("pablo-verify=test-token")
    expect(within(row()).queryByText("Copied")).toBeNull()
  })
})

describe("DomainsPage Check now", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockStatus.mockResolvedValue({ is_practice_owner: true })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("shows the check in flight, then its result, and rests for 30 seconds", async () => {
    mockList.mockResolvedValue({ domains: [host()] })
    let answer: (value: unknown) => void = () => {}
    mockCheck.mockImplementation(() => new Promise((resolve) => (answer = resolve)))
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderWithProviders(<DomainsPage />)

    await user.click(await screen.findByRole("button", { name: "Check now" }))
    const inFlight = screen.getByRole("button", { name: "Checking…" })
    expect(inFlight).toBeDisabled()

    const missing = host({ dns_records: host().dns_records.map((r) => ({ ...r, check: "missing" as const, found: [] })) })
    await act(async () => answer({ domains: [missing] }))

    expect(await screen.findByText("Checked just now. You can check again in a moment.")).toBeVisible()
    expect(screen.getByTestId("check-TXT-_pablo-verify.example.com")).toHaveTextContent("Not found yet")
    expect(screen.getByRole("button", { name: "Check now" })).toBeDisabled()
    // Records still to add are the practice's to do, not the server's.
    expect(screen.queryByTestId("domain-finishing")).toBeNull()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(screen.getByRole("button", { name: "Check now" })).toBeEnabled()
    expect(screen.getByTestId("domains-check-note")).toHaveTextContent(/^Last checked at /)
  })

  it("lets a failed check be tried again at once", async () => {
    mockList.mockResolvedValue({ domains: [host()] })
    mockCheck.mockRejectedValue(new Error("network"))
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderWithProviders(<DomainsPage />)

    await user.click(await screen.findByRole("button", { name: "Check now" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Your records couldn't be checked. Try again.")
    expect(screen.getByRole("button", { name: "Check now" })).toBeEnabled()
  })

  it("polls while setup finishes, and stops once the host is active", async () => {
    mockList.mockResolvedValue({ domains: [host()] })
    mockCheck.mockResolvedValue({ domains: [allFound({ status: "verifying" })] })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderWithProviders(<DomainsPage />)

    await user.click(await screen.findByRole("button", { name: "Check now" }))
    expect(await screen.findByTestId("domain-finishing")).toHaveTextContent(
      "Finishing setup — this usually takes a few minutes.",
    )
    expect(within(row()).getByText("Checking")).toBeVisible()
    expect(screen.queryByText("Active")).toBeNull()
    const listsBefore = mockList.mock.calls.length

    // Still verifying at the first poll: the check's results stay on screen.
    mockList.mockResolvedValue({ domains: [host({ status: "verifying" })] })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000)
    })
    expect(mockList.mock.calls.length).toBe(listsBefore + 1)
    expect(screen.getByTestId("check-TXT-_pablo-verify.example.com")).toHaveTextContent("Found")
    expect(screen.getByTestId("domain-finishing")).toBeVisible()

    mockList.mockResolvedValue({ domains: [host({ status: "active" })] })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000)
    })
    expect(within(row()).getByText("Active")).toBeVisible()
    expect(screen.queryByTestId("domain-finishing")).toBeNull()

    const listsWhenActive = mockList.mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(mockList.mock.calls.length).toBe(listsWhenActive)
  })

  it("stops polling after ten minutes", async () => {
    mockList.mockResolvedValue({ domains: [host({ status: "verifying" })] })
    mockCheck.mockResolvedValue({ domains: [allFound({ status: "verifying" })] })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderWithProviders(<DomainsPage />)

    await user.click(await screen.findByRole("button", { name: "Check now" }))
    await screen.findByTestId("domain-finishing")
    const listsBefore = mockList.mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60_000 + 15_000)
    })
    const atDeadline = mockList.mock.calls.length
    // Every 15 seconds for ten minutes.
    expect(atDeadline - listsBefore).toBeGreaterThanOrEqual(39)
    expect(atDeadline - listsBefore).toBeLessThanOrEqual(41)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(mockList.mock.calls.length).toBe(atDeadline)
  })

  it("doesn't poll when nothing is left in progress", async () => {
    mockList.mockResolvedValue({ domains: [host({ status: "error" })] })
    mockCheck.mockResolvedValue({ domains: [allFound({ status: "error" })] })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderWithProviders(<DomainsPage />)

    await user.click(await screen.findByRole("button", { name: "Check now" }))
    await screen.findByText("Checked just now. You can check again in a moment.")
    const after = mockList.mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(mockList.mock.calls.length).toBe(after)
  })
})

describe("DomainsPage portal address with the portal off", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStatus.mockResolvedValue({ is_practice_owner: true })
  })

  it("says to turn the portal on, and links to it, while the practice doesn't offer it", async () => {
    mockList.mockResolvedValue({ domains: [host({ status: "active" })] })
    mockPortalSettings.mockResolvedValue({ enabled: false, decided: true, modules: {} })
    renderWithProviders(<DomainsPage />)

    const note = await screen.findByTestId("domains-portal-off")
    expect(note).toHaveTextContent("Turn on the portal in Patient portal before these addresses can show it.")
    expect(within(note).getByRole("link", { name: "Patient portal" })).toHaveAttribute(
      "href",
      "/dashboard/settings/portal",
    )
  })

  it("says nothing when the portal is on, or there is no portal address", async () => {
    mockList.mockResolvedValue({ domains: [host({ status: "active" })] })
    mockPortalSettings.mockResolvedValue({ enabled: true, decided: true, modules: {} })
    const { unmount } = renderWithProviders(<DomainsPage />)
    await screen.findByTestId("domain-row-portal.example.com")
    await vi.waitFor(() => expect(mockPortalSettings).toHaveBeenCalled())
    expect(screen.queryByTestId("domains-portal-off")).toBeNull()
    unmount()

    mockPortalSettings.mockClear()
    mockPortalSettings.mockResolvedValue({ enabled: false, decided: true, modules: {} })
    mockList.mockResolvedValue({ domains: [host({ purpose: "site" })] })
    renderWithProviders(<DomainsPage />)
    await screen.findByTestId("domain-row-portal.example.com")
    expect(mockPortalSettings).not.toHaveBeenCalled()
    expect(screen.queryByTestId("domains-portal-off")).toBeNull()
  })
})
