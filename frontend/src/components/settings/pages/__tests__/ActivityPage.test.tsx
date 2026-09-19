// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * You > Your activity.
 *
 * What matters here is what the privacy policy promises: the account holder
 * can read their own audit log, see the request context that would let them
 * recognise access they did not make, and get past the first page — without
 * the page implying the first page is the whole history.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactElement } from "react"

import { ActivityPage } from "../ActivityPage"
import type { AuditLogPage } from "@/lib/api/users"

const mockGetMyAuditLog = vi.fn()

vi.mock("@/lib/api/users", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/users")>("@/lib/api/users")
  return {
    ...actual,
    getMyAuditLog: (...args: unknown[]) => mockGetMyAuditLog(...args),
  }
})

function renderPage(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

function row(overrides: Record<string, unknown> = {}) {
  return {
    id: `row-${Math.random()}`,
    timestamp: "2026-03-01T12:30:00Z",
    actor_type: "clinician",
    action: "patient_viewed",
    resource_type: "patient",
    resource_id: "patient-123",
    patient_id: "patient-123",
    session_id: null,
    ip_address: "203.0.113.4",
    user_agent: "Mozilla/5.0 (Macintosh) Chrome/131.0.0.0 Safari/537.36",
    ...overrides,
  }
}

function page(rows: ReturnType<typeof row>[], next: string | null = null): AuditLogPage {
  return { data: rows as AuditLogPage["data"], limit: 50, next_cursor: next }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("ActivityPage", () => {
  it("shows an entry with the context that makes it recognisable", async () => {
    mockGetMyAuditLog.mockResolvedValue(page([row()]))

    renderPage(<ActivityPage />)

    expect(await screen.findByText("Patient viewed")).toBeInTheDocument()
    expect(screen.getByText("Patient patient-123")).toBeInTheDocument()
    expect(screen.getByText("203.0.113.4")).toBeInTheDocument()
    expect(screen.getByText("Chrome")).toBeInTheDocument()
  })

  it("says when a row in your trail was not your own action", async () => {
    mockGetMyAuditLog.mockResolvedValue(
      page([row({ action: "patient_created", actor_type: "anonymous" })]),
    )

    renderPage(<ActivityPage />)

    expect(await screen.findByText("Patient created")).toBeInTheDocument()
    expect(screen.getByText("Anonymous")).toBeInTheDocument()
  })

  it("loads older rows when asked, and stops claiming there are more", async () => {
    const user = userEvent.setup()
    mockGetMyAuditLog
      .mockResolvedValueOnce(page([row({ action: "patient_viewed" })], "cursor-1"))
      .mockResolvedValueOnce(page([row({ action: "session_created" })], null))

    renderPage(<ActivityPage />)

    // While there may be more, the page says what it is showing rather than
    // implying this is everything.
    expect(await screen.findByText(/Showing the 1 most recent/)).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /Load older/ }))

    expect(await screen.findByText("Session created")).toBeInTheDocument()
    expect(screen.getByText("Patient viewed")).toBeInTheDocument()
    expect(screen.getByText(/Showing all 2/)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Load older/ })).not.toBeInTheDocument()
    // The second request carried the cursor from the first.
    expect(mockGetMyAuditLog).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: "cursor-1" }),
    )
  })

  it("does not refetch itself — reading the log writes to the log", async () => {
    mockGetMyAuditLog.mockResolvedValue(page([row()]))

    renderPage(<ActivityPage />)
    await screen.findByText("Patient viewed")

    // Give any timer- or focus-driven refetch a chance to fire.
    window.dispatchEvent(new Event("focus"))
    await new Promise((resolve) => setTimeout(resolve, 50))

    expect(mockGetMyAuditLog).toHaveBeenCalledTimes(1)
  })

  it("has an empty state that does not read as a failure", async () => {
    mockGetMyAuditLog.mockResolvedValue(page([]))

    renderPage(<ActivityPage />)

    expect(await screen.findByText("Nothing recorded yet")).toBeInTheDocument()
  })

  it("distinguishes a page that could not load from a record that is not there", async () => {
    mockGetMyAuditLog.mockRejectedValue(new Error("network"))

    renderPage(<ActivityPage />)

    expect(await screen.findByText("Your activity could not be loaded")).toBeInTheDocument()
    expect(screen.getByText(/The record is intact/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Try again/ })).toBeInTheDocument()
  })

  it("retries on demand", async () => {
    const user = userEvent.setup()
    mockGetMyAuditLog
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(page([row()]))

    renderPage(<ActivityPage />)
    await screen.findByRole("button", { name: /Try again/ })

    await user.click(screen.getByRole("button", { name: /Try again/ }))

    await waitFor(() => expect(screen.getByText("Patient viewed")).toBeInTheDocument())
  })
})
