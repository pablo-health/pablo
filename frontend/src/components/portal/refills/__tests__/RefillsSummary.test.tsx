// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * RefillsSummary: the refills tile's line on the portal home screen, and
 * the promise that opening the section after it does not ask again.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { RefillRequest } from "@/lib/api/patientRefills"
import * as api from "@/lib/api/patientRefills"
import { PortalRefills } from "../PortalRefills"
import { RefillsSummary, refillsSummaryLine } from "../RefillsSummary"

vi.mock("@/lib/api/patientRefills", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/patientRefills")>()
  return {
    ...actual,
    listRefillMedications: vi.fn(),
    listRefillRequests: vi.fn(),
    createRefillRequest: vi.fn(),
  }
})

// The refills section reads the capability document for the practice's name;
// this file is about the request list, so the section gets its fallback.
vi.mock("@/lib/portal-shell/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/portal-shell/api")>()
  return { ...actual, fetchCapabilities: vi.fn().mockResolvedValue({ ok: false }) }
})

const TOKEN = "session-token"

function request(overrides: Partial<RefillRequest>): RefillRequest {
  return {
    id: "r-1",
    medication_id: null,
    medication_text: "Sertraline 50 mg",
    pharmacy_text: null,
    patient_note: null,
    status: "requested",
    created_at: "2026-09-20T15:00:00Z",
    decided_at: null,
    ...overrides,
  }
}

function renderWithClient(ui: React.ReactNode, client = new QueryClient()) {
  const utils = render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
  return { ...utils, client }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.listRefillMedications).mockResolvedValue({ data: [], total: 0 })
})

describe("refillsSummaryLine", () => {
  it("says so when nothing has been asked for", () => {
    expect(refillsSummaryLine([])).toBe("No refill requests")
  })

  it("names the latest request's status and the day it was decided", () => {
    const line = refillsSummaryLine([
      request({ id: "old", status: "declined", created_at: "2026-08-01T15:00:00Z" }),
      request({
        id: "new",
        status: "approved",
        created_at: "2026-09-26T15:00:00Z",
        decided_at: "2026-09-28T15:00:00Z",
      }),
    ])
    expect(line).toMatch(/^Sent to your pharmacy · /)
    expect(line).toContain("28")
  })

  it("dates a request still waiting by when it was asked for", () => {
    const line = refillsSummaryLine([request({ created_at: "2026-09-20T15:00:00Z" })])
    expect(line).toMatch(/^Received · /)
    expect(line).toContain("20")
  })
})

describe("RefillsSummary", () => {
  it("renders the line once the list arrives", async () => {
    vi.mocked(api.listRefillRequests).mockResolvedValue({
      data: [request({ status: "needs_visit", decided_at: "2026-09-21T15:00:00Z" })],
      total: 1,
    })

    renderWithClient(<RefillsSummary sessionToken={TOKEN} />)

    expect(await screen.findByText(/^Let's talk at your next visit · /)).toBeTruthy()
  })

  it("renders nothing when the list fails to load", async () => {
    vi.mocked(api.listRefillRequests).mockRejectedValue(new Error("boom"))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const { container } = renderWithClient(<RefillsSummary sessionToken={TOKEN} />, client)

    await waitFor(() => expect(api.listRefillRequests).toHaveBeenCalled())
    expect(container.textContent).toBe("")
  })

  it("shares its cache with the refills section, so opening it does not fetch again", async () => {
    vi.mocked(api.listRefillRequests).mockResolvedValue({
      data: [request({ status: "approved", decided_at: "2026-09-28T15:00:00Z" })],
      total: 1,
    })
    const client = new QueryClient({ defaultOptions: { queries: { staleTime: 60_000 } } })

    const { unmount } = renderWithClient(<RefillsSummary sessionToken={TOKEN} />, client)
    await screen.findByText(/^Sent to your pharmacy/)
    unmount()

    renderWithClient(<PortalRefills sessionToken={TOKEN} />, client)
    await screen.findByTestId("portal-refills-list")

    expect(api.listRefillRequests).toHaveBeenCalledTimes(1)
  })
})
