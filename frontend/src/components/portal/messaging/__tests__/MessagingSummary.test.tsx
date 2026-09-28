// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/** MessagingSummary: the messaging tile's line on the portal home screen. */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { PatientMessageThread } from "@/lib/api/patientMessages"
import * as api from "@/lib/api/patientMessages"
import { MessagingSummary, messagingSummaryLine } from "../MessagingSummary"

vi.mock("@/lib/api/patientMessages")

const TOKEN = "session-token"

function thread(id: string, unread: number | null | undefined): PatientMessageThread {
  return {
    id,
    subject: null,
    status: "open",
    created_at: "2026-09-20T12:00:00Z",
    last_message_at: "2026-09-21T12:00:00Z",
    unread_count: unread,
  }
}

function renderSummary() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MessagingSummary sessionToken={TOKEN} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("messagingSummaryLine", () => {
  it("reads No new messages at zero and a count otherwise", () => {
    expect(messagingSummaryLine(0)).toBe("No new messages")
    expect(messagingSummaryLine(3)).toBe("3 unread")
  })
})

describe("MessagingSummary", () => {
  it("adds up the unread count across threads", async () => {
    vi.mocked(api.listThreads).mockResolvedValue({
      data: [thread("t1", 2), thread("t2", 1), thread("t3", null), thread("t4", undefined)],
      total: 4,
    })

    renderSummary()

    expect(await screen.findByText("3 unread")).toBeTruthy()
  })

  it("says so when nothing is unread", async () => {
    vi.mocked(api.listThreads).mockResolvedValue({ data: [thread("t1", 0)], total: 1 })

    renderSummary()

    expect(await screen.findByText("No new messages")).toBeTruthy()
  })

  it("renders nothing when the list fails to load", async () => {
    vi.mocked(api.listThreads).mockRejectedValue(new Error("boom"))

    const { container } = renderSummary()

    await waitFor(() => expect(api.listThreads).toHaveBeenCalled())
    expect(container.textContent).toBe("")
  })
})
