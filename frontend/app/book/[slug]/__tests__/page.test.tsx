// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import PublicBookingPage from "../page"

vi.mock("next/image", () => ({
  default: ({ src, alt, ...props }: { src: string; alt: string; [k: string]: unknown }) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={src} alt={alt} {...props} />
  ),
}))

const CAPTCHA_FAILED_MESSAGE = "Verification failed. Please refresh and try again."
const NOT_ACCEPTING_MESSAGE =
  "This practice isn't accepting online bookings right now. Please contact them directly."

const CARD = {
  slug: "test-link",
  host_name: "Test Therapist",
  title: "Intro call",
  description: "A get-to-know-you call.",
  duration_minutes: 30,
}

const SLOTS = {
  date: "2026-09-02",
  slots: [{ start: "2026-09-02T09:30:00Z", end: "2026-09-02T10:00:00Z" }],
  configured: true,
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as Response
}

function mockFetch(opts: {
  captchaSiteKey?: string | null
  postStatus?: number
  postBody?: unknown
}) {
  const calls: { url: string; init?: RequestInit }[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString()
    calls.push({ url, init })
    if (url.includes("/bookings") && init?.method === "POST") {
      return jsonResponse(
        opts.postBody ?? {
          host_name: CARD.host_name,
          title: CARD.title,
          start_at: SLOTS.slots[0].start,
          end_at: SLOTS.slots[0].end,
          duration_minutes: CARD.duration_minutes,
        },
        opts.postStatus ?? 201,
      )
    }
    if (url.includes("/slots")) {
      return jsonResponse(SLOTS)
    }
    if (url.includes("/booking-links/")) {
      return jsonResponse({ ...CARD, captcha_site_key: opts.captchaSiteKey ?? null })
    }
    return jsonResponse({})
  })
  return { fetchMock, calls }
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <PublicBookingPage params={Promise.resolve({ slug: "test-link" })} />
    </QueryClientProvider>,
  )
}

async function fillAndSelectSlot() {
  const slotButton = await screen.findByRole("button", { name: /9:30 AM/i })
  await userEvent.click(slotButton)
  await userEvent.type(screen.getByLabelText(/first name/i), "Jane")
  await userEvent.type(screen.getByLabelText(/last name/i), "Roe")
  await userEvent.type(screen.getByLabelText(/email/i), "jane@example.com")
}

describe("PublicBookingPage — pending confirmation", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("renders the check-your-email copy and hides the .ics button when a hold is pending", async () => {
    const { fetchMock } = mockFetch({
      postBody: {
        host_name: CARD.host_name,
        title: CARD.title,
        start_at: SLOTS.slots[0].start,
        end_at: SLOTS.slots[0].end,
        duration_minutes: CARD.duration_minutes,
        status: "pending_confirmation",
      },
      postStatus: 201,
    })
    vi.stubGlobal("fetch", fetchMock)

    renderPage()
    await fillAndSelectSlot()
    await userEvent.click(screen.getByRole("button", { name: /confirm booking/i }))

    await waitFor(() => {
      expect(
        screen.getByText(/check your email to confirm.*your hold expires in 15 minutes/i),
      ).toBeInTheDocument()
    })
    expect(screen.queryByRole("button", { name: /add to calendar/i })).not.toBeInTheDocument()
  })
})

describe("PublicBookingPage — CAPTCHA", () => {
  beforeEach(() => {
    delete (window as { turnstile?: unknown }).turnstile
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("renders no widget container and submits normally when the card has no site key", async () => {
    const { fetchMock } = mockFetch({ captchaSiteKey: null })
    vi.stubGlobal("fetch", fetchMock)

    renderPage()
    await fillAndSelectSlot()

    const form = screen.getByRole("button", { name: /confirm booking/i }).closest("form")!
    expect(within(form).queryByRole("button", { name: /confirm booking/i })).not.toBeDisabled()

    await userEvent.click(screen.getByRole("button", { name: /confirm booking/i }))

    await waitFor(() => {
      expect(screen.getByText(/you.re booked/i)).toBeInTheDocument()
    })

    const postCall = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "POST",
    )
    const headers = (postCall?.[1] as RequestInit)?.headers as Record<string, string>
    expect(headers["X-Captcha-Token"]).toBeUndefined()
  })

  it("mounts the widget, disables submit until the token callback fires, and sends the token header", async () => {
    const { fetchMock } = mockFetch({ captchaSiteKey: "site-key-abc" })
    vi.stubGlobal("fetch", fetchMock)

    let renderCallback: ((token: string) => void) | null = null
    const resetMock = vi.fn()
    window.turnstile = {
      render: vi.fn((_container: HTMLElement, options: { callback: (token: string) => void }) => {
        renderCallback = options.callback
        return "widget-1"
      }),
      reset: resetMock,
    }

    renderPage()
    await fillAndSelectSlot()

    await waitFor(() => expect(window.turnstile!.render).toHaveBeenCalled())

    const submitButton = screen.getByRole("button", { name: /confirm booking/i })
    expect(submitButton).toBeDisabled()

    expect(renderCallback).not.toBeNull()
    renderCallback!("captcha-token-xyz")

    await waitFor(() => expect(submitButton).not.toBeDisabled())

    await userEvent.click(submitButton)

    await waitFor(() => {
      expect(screen.getByText(/you.re booked/i)).toBeInTheDocument()
    })

    const postCall = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "POST",
    )
    const headers = (postCall?.[1] as RequestInit)?.headers as Record<string, string>
    expect(headers["X-Captcha-Token"]).toBe("captcha-token-xyz")
  })

  it("resets the widget and shows the verification message on a 403 captcha failure", async () => {
    const { fetchMock } = mockFetch({
      captchaSiteKey: "site-key-abc",
      postStatus: 403,
      postBody: { error: { code: "FORBIDDEN", message: CAPTCHA_FAILED_MESSAGE, details: {} } },
    })
    vi.stubGlobal("fetch", fetchMock)

    let renderCallback: ((token: string) => void) | null = null
    const resetMock = vi.fn()
    window.turnstile = {
      render: vi.fn((_container: HTMLElement, options: { callback: (token: string) => void }) => {
        renderCallback = options.callback
        return "widget-1"
      }),
      reset: resetMock,
    }

    renderPage()
    await fillAndSelectSlot()
    await waitFor(() => expect(window.turnstile!.render).toHaveBeenCalled())
    renderCallback!("captcha-token-xyz")

    const submitButton = screen.getByRole("button", { name: /confirm booking/i })
    await waitFor(() => expect(submitButton).not.toBeDisabled())
    await userEvent.click(submitButton)

    await waitFor(() => {
      expect(screen.getByText(CAPTCHA_FAILED_MESSAGE)).toBeInTheDocument()
    })
    expect(screen.queryByText(NOT_ACCEPTING_MESSAGE)).not.toBeInTheDocument()
    expect(resetMock).toHaveBeenCalledWith("widget-1")
  })
})
