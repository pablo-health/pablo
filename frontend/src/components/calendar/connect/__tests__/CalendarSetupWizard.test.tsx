// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type {
  GoogleCalendarConsentOptions,
  GoogleCalendarStatus,
} from "@/lib/api/scheduling"
import { CalendarSetupWizard } from "../CalendarSetupWizard"

const searchParams = new URLSearchParams()
const routerReplace = vi.fn()
const routerPush = vi.fn()
// One stable object: a fresh router identity per render would restart every
// effect that depends on it, which is not how next/navigation behaves.
const router = { replace: routerReplace, push: routerPush }

vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useSearchParams: () => searchParams,
}))

const getStatus = vi.fn<() => Promise<GoogleCalendarStatus>>()
const getConsentOptions = vi.fn<() => Promise<GoogleCalendarConsentOptions>>()
const getAuthUrl = vi.fn()
const completeConnect = vi.fn()
const disconnect = vi.fn()

vi.mock("@/lib/api/scheduling", () => ({
  getGoogleCalendarStatus: () => getStatus(),
  getGoogleCalendarConsentOptions: () => getConsentOptions(),
  getGoogleCalendarAuthUrl: (...args: unknown[]) => getAuthUrl(...args),
  completeGoogleCalendarConnect: (...args: unknown[]) => completeConnect(...args),
  disconnectGoogleCalendar: () => disconnect(),
}))

const DISCONNECTED: GoogleCalendarStatus = {
  connected: false,
  calendar_id: null,
  last_synced_at: null,
  write_target: null,
}

const CONSENT_OPTIONS: GoogleCalendarConsentOptions = {
  write_targets: [
    { id: "app_calendar", promise: "Google Calendar limits this to the calendar Pablo creates." },
    { id: "primary", promise: "Pablo uses this only for the sessions you book in Pablo." },
  ],
  busy: { id: "busy", promise: "Google Calendar limits this to when you are busy." },
  default_write_target: "app_calendar",
  busy_default: true,
}

function renderWizard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <CalendarSetupWizard />
    </QueryClientProvider>
  )
}

async function goToSessionsStep(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /sessions/i }))
  await screen.findByText("Where your sessions go")
}

describe("CalendarSetupWizard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.sessionStorage.clear()
    getStatus.mockResolvedValue(DISCONNECTED)
    getConsentOptions.mockResolvedValue(CONSENT_OPTIONS)
    getAuthUrl.mockResolvedValue({ auth_url: "https://accounts.google.com/o/oauth2/auth?x=1" })
    Object.defineProperty(window, "location", {
      value: { origin: "https://app.example.test", assign: vi.fn() },
      writable: true,
    })
  })

  it("connects with the recommended choice: a calendar Pablo makes, plus busy times", async () => {
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole("button", { name: /connect google calendar/i }))

    await waitFor(() => expect(getAuthUrl).toHaveBeenCalled())
    expect(getAuthUrl.mock.calls[0][1]).toEqual({ write_target: "app_calendar", busy: true })
    expect(window.location.assign).toHaveBeenCalledWith(
      "https://accounts.google.com/o/oauth2/auth?x=1"
    )
  })

  it("asks for the main calendar only when that is chosen", async () => {
    const user = userEvent.setup()
    renderWizard()
    await goToSessionsStep(user)

    await user.click(screen.getByRole("radio", { name: /my main calendar/i }))
    await user.click(screen.getByRole("button", { name: /connect google calendar/i }))

    await waitFor(() => expect(getAuthUrl).toHaveBeenCalled())
    expect(getAuthUrl.mock.calls[0][1]).toEqual({ write_target: "primary", busy: true })
  })

  it("does not ask for busy times when that is unchecked", async () => {
    const user = userEvent.setup()
    renderWizard()
    await goToSessionsStep(user)

    await user.click(screen.getByRole("checkbox", { name: /also check when i'm busy/i }))
    await user.click(screen.getByRole("button", { name: /connect google calendar/i }))

    await waitFor(() => expect(getAuthUrl).toHaveBeenCalled())
    expect(getAuthUrl.mock.calls[0][1]).toEqual({ write_target: "app_calendar", busy: false })
  })

  it("shows each choice's promise as the API generated it", async () => {
    const user = userEvent.setup()
    renderWizard()
    await goToSessionsStep(user)

    expect(
      screen.getByText("Google Calendar limits this to the calendar Pablo creates.")
    ).toBeInTheDocument()
    expect(
      screen.getByText("Pablo uses this only for the sessions you book in Pablo.")
    ).toBeInTheDocument()
    expect(
      screen.getByText("Google Calendar limits this to when you are busy.")
    ).toBeInTheDocument()
  })

  it("never shows a Google permission name", async () => {
    const user = userEvent.setup()
    const { container } = renderWizard()
    await goToSessionsStep(user)

    expect(container.textContent).not.toContain("calendar.app.created")
    expect(container.textContent).not.toContain("calendar.events")
    expect(container.textContent).not.toContain("calendar.readonly")
    expect(container.textContent).not.toContain("googleapis.com")
  })

  it("shows the connected calendar and disconnects it", async () => {
    getStatus.mockResolvedValue({
      connected: true,
      calendar_id: "pablo-made@group.calendar.google.com",
      last_synced_at: null,
      write_target: "app_calendar",
    })
    disconnect.mockResolvedValue({ status: "disconnected" })
    const user = userEvent.setup()
    renderWizard()

    expect(await screen.findByText("pablo-made@group.calendar.google.com")).toBeInTheDocument()
    expect(screen.getByText("A calendar Pablo made for your sessions")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /disconnect/i }))

    await waitFor(() => expect(disconnect).toHaveBeenCalled())
  })

  it("surfaces a failure to start the connection instead of leaving a dead button", async () => {
    getAuthUrl.mockRejectedValue(new Error("Google is unreachable"))
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole("button", { name: /connect google calendar/i }))

    expect(await screen.findByText("Google is unreachable")).toBeInTheDocument()
  })
})

describe("CalendarSetupWizard returning from Google", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.sessionStorage.clear()
    getStatus.mockResolvedValue(DISCONNECTED)
    getConsentOptions.mockResolvedValue(CONSENT_OPTIONS)
    completeConnect.mockResolvedValue({ status: "connected" })
    searchParams.set("code", "auth-code")
    searchParams.set("state", "state-from-google")
    Object.defineProperty(window, "location", {
      value: { origin: "https://app.example.test", assign: vi.fn() },
      writable: true,
    })
  })

  it("exchanges the code with the choice that was made before the redirect", async () => {
    window.sessionStorage.setItem(
      "pablo.calendar-connect.selection",
      JSON.stringify({ write_target: "primary", busy: false })
    )

    renderWizard()

    await waitFor(() => expect(completeConnect).toHaveBeenCalled())
    const [code, state, redirectUri, selection] = completeConnect.mock.calls[0]
    expect(code).toBe("auth-code")
    // The backend checks this was minted for the signed-in user, so it has
    // to survive the round trip rather than being dropped here.
    expect(state).toBe("state-from-google")
    expect(redirectUri).toBe("https://app.example.test/dashboard/settings/calendar")
    expect(selection).toEqual({ write_target: "primary", busy: false })
    // The one-time code must not survive a refresh.
    await waitFor(() =>
      expect(routerReplace).toHaveBeenCalledWith("/dashboard/settings/calendar")
    )
  })
})
