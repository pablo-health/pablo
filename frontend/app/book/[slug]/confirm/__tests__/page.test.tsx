// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import ConfirmBookingPage from "../page"

vi.mock("next/image", () => ({
  default: ({ src, alt, ...props }: { src: string; alt: string; [k: string]: unknown }) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={src} alt={alt} {...props} />
  ),
}))

let searchParams = new URLSearchParams("token=abc123")

vi.mock("next/navigation", () => ({
  useSearchParams: () => searchParams,
}))

const CONFIRMATION = {
  host_name: "Test Therapist",
  title: "Intro call",
  start_at: "2026-09-02T09:30:00Z",
  end_at: "2026-09-02T10:00:00Z",
  duration_minutes: 30,
  status: "confirmed" as const,
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as Response
}

function renderPage() {
  return render(<ConfirmBookingPage params={Promise.resolve({ slug: "test-link" })} />)
}

describe("ConfirmBookingPage", () => {
  beforeEach(() => {
    searchParams = new URLSearchParams("token=abc123")
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("POSTs the token from the query string to the confirm endpoint", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse(CONFIRMATION),
    )
    vi.stubGlobal("fetch", fetchMock)

    renderPage()

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain("/api/public/booking-links/test-link/confirm")
    expect(init?.method).toBe("POST")
    expect(JSON.parse(init?.body as string)).toEqual({ token: "abc123" })
  })

  it("renders the confirmed card on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(CONFIRMATION)),
    )

    renderPage()

    await waitFor(() => {
      expect(screen.getByText(/you.re booked/i)).toBeInTheDocument()
    })
    expect(screen.getByRole("button", { name: /add to calendar/i })).toBeInTheDocument()
  })

  it("renders the invalid-link copy on a 404", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error: { code: "NOT_FOUND" } }, 404)),
    )

    renderPage()

    await waitFor(() => {
      expect(screen.getByText("This confirmation link is not valid.")).toBeInTheDocument()
    })
  })

  it("renders the slot-taken copy with a link back to the booking page on a 409", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error: { code: "CONFLICT" } }, 409)),
    )

    renderPage()

    await waitFor(() => {
      expect(
        screen.getByText(
          "That time was taken while you were confirming. Please pick another slot.",
        ),
      ).toBeInTheDocument()
    })
    const link = screen.getByRole("link", { name: /pick another time/i })
    expect(link).toHaveAttribute("href", "/book/test-link")
  })

  it("renders a retry option on a network error and retries on click", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce(jsonResponse(CONFIRMATION))
    vi.stubGlobal("fetch", fetchMock)

    renderPage()

    const retryButton = await screen.findByRole("button", { name: /retry/i })
    await userEvent.click(retryButton)

    await waitFor(() => {
      expect(screen.getByText(/you.re booked/i)).toBeInTheDocument()
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
