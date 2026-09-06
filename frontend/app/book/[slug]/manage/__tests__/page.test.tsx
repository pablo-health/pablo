// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import ManageBookingPage from "../page"

let searchParams = new URLSearchParams("token=abc123")

vi.mock("next/navigation", () => ({
  useSearchParams: () => searchParams,
}))

const BOOKING = {
  title: "Intro call",
  host_name: "Test Therapist",
  start_at: "2026-09-02T09:30:00Z",
  end_at: "2026-09-02T10:00:00Z",
  duration_minutes: 30,
  status: "confirmed",
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as Response
}

function renderPage() {
  return render(<ManageBookingPage params={Promise.resolve({ slug: "test-link" })} />)
}

describe("ManageBookingPage", () => {
  beforeEach(() => {
    searchParams = new URLSearchParams("token=abc123")
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("renders the booking card from a mocked 200", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(BOOKING)),
    )

    renderPage()

    await waitFor(() => {
      expect(screen.getByText("Intro call with Test Therapist")).toBeInTheDocument()
    })
    expect(screen.getByRole("button", { name: /cancel appointment/i })).toBeInTheDocument()
  })

  it("requires a confirm click before cancelling, then POSTs cancel once", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      if (String(input).includes("/manage/cancel")) {
        return jsonResponse({ cancelled: true })
      }
      return jsonResponse(BOOKING)
    })
    vi.stubGlobal("fetch", fetchMock)

    renderPage()

    const cancelButton = await screen.findByRole("button", { name: /cancel appointment/i })
    await userEvent.click(cancelButton)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const confirmButton = await screen.findByRole("button", { name: /yes, cancel it/i })
    await userEvent.click(confirmButton)

    await waitFor(() => {
      expect(screen.getByText("Appointment cancelled")).toBeInTheDocument()
    })

    const cancelCalls = fetchMock.mock.calls.filter(([input]) =>
      String(input).includes("/manage/cancel"),
    )
    expect(cancelCalls).toHaveLength(1)
    expect(cancelCalls[0][1]?.method).toBe("POST")
    expect(JSON.parse(cancelCalls[0][1]?.body as string)).toEqual({ token: "abc123" })
  })

  it("renders the generic not-valid state on a 404, with no other detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error: { code: "NOT_FOUND" } }, 404)),
    )

    renderPage()

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /link isn.t valid/i })).toBeInTheDocument()
    })
    expect(
      screen.getByText("This link is not valid or has expired."),
    ).toBeInTheDocument()
    expect(screen.queryByText(/NOT_FOUND/)).not.toBeInTheDocument()
  })
})
