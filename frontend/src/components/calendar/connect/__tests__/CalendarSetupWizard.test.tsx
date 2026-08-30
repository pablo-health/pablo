// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type {
  ConfirmImportResult,
  GoogleCalendarConsentOptions,
  GoogleCalendarStatus,
  ImportConsentRequired,
  ImportProposal,
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
const getBusyWindows = vi.fn()
const scanForImport = vi.fn<(...args: unknown[]) => Promise<ImportProposal | ImportConsentRequired>>()
const completeImportConsent = vi.fn()
const confirmImport = vi.fn<(...args: unknown[]) => Promise<ConfirmImportResult>>()

vi.mock("@/lib/api/scheduling", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/api/scheduling")>("@/lib/api/scheduling")
  return {
    // Pure helpers — the real implementations, not mocked away.
    importNeedsConsent: actual.importNeedsConsent,
    busyWindowsGranted: actual.busyWindowsGranted,
    getGoogleCalendarStatus: () => getStatus(),
    getGoogleCalendarConsentOptions: () => getConsentOptions(),
    getGoogleCalendarAuthUrl: (...args: unknown[]) => getAuthUrl(...args),
    completeGoogleCalendarConnect: (...args: unknown[]) => completeConnect(...args),
    disconnectGoogleCalendar: () => disconnect(),
    getCalendarBusyWindows: (...args: unknown[]) => getBusyWindows(...args),
    scanCalendarForImport: (...args: unknown[]) => scanForImport(...args),
    completeGoogleCalendarImportConsent: (...args: unknown[]) => completeImportConsent(...args),
    confirmCalendarImport: (...args: unknown[]) => confirmImport(...args),
  }
})

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

async function goToClientsStep(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /your clients/i }))
  await screen.findByText("Bring over your week")
}

const CONNECTED: GoogleCalendarStatus = {
  connected: true,
  calendar_id: "pablo-made@group.calendar.google.com",
  last_synced_at: null,
  write_target: "app_calendar",
}

function proposalWith(overrides: Partial<ImportProposal> = {}): ImportProposal {
  return {
    series: [
      {
        candidate_key: "a",
        summary: "Jane Miller",
        weekday: 0,
        local_start_time: "09:00",
        duration_minutes: 50,
        cadence: "weekly",
        occurrences_in_window: 8,
        occurrences_ahead: 4,
        first_future_start: "2026-09-07T09:00:00Z",
        last_seen: "2026-08-31T09:00:00Z",
        recurrence_rule: "RRULE:FREQ=WEEKLY",
        status: "active",
        confidence: 0.9,
        preselected: true,
      },
      {
        candidate_key: "b",
        summary: "Standup",
        weekday: 2,
        local_start_time: "10:00",
        duration_minutes: 30,
        cadence: "weekly",
        occurrences_in_window: 8,
        occurrences_ahead: 4,
        first_future_start: "2026-09-09T10:00:00Z",
        last_seen: "2026-08-31T10:00:00Z",
        recurrence_rule: "RRULE:FREQ=WEEKLY",
        status: "active",
        confidence: 0.4,
        preselected: false,
      },
    ],
    left_alone: 3,
    events_read: 40,
    partial: false,
    lookback_days: 90,
    horizon_days: 90,
    timezone: "UTC",
    ...overrides,
  }
}

describe("CalendarSetupWizard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.sessionStorage.clear()
    getStatus.mockResolvedValue(DISCONNECTED)
    getConsentOptions.mockResolvedValue(CONSENT_OPTIONS)
    getAuthUrl.mockResolvedValue({ auth_url: "https://accounts.google.com/o/oauth2/auth?x=1" })
    getBusyWindows.mockResolvedValue({ windows: [] })
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

  it("skipping the week completes the wizard without ever scanning", async () => {
    getStatus.mockResolvedValue(CONNECTED)
    const user = userEvent.setup()
    renderWizard()
    await goToClientsStep(user)

    await user.click(screen.getByRole("button", { name: /skip, i.ll add them myself/i }))

    expect(routerPush).toHaveBeenCalledWith("/dashboard/settings")
    expect(scanForImport).not.toHaveBeenCalled()
  })

  it("scans, reviews, and confirms only the checked series", async () => {
    getStatus.mockResolvedValue(CONNECTED)
    scanForImport.mockResolvedValue(proposalWith())
    confirmImport.mockResolvedValue({
      confirmed: [{ candidate_key: "a", patient_id: "p-1", appointments_created: 4 }],
      patients_created: 1,
      appointments_created: 4,
      skipped: [],
    })
    const user = userEvent.setup()
    renderWizard()
    await goToClientsStep(user)

    await user.click(screen.getByRole("button", { name: "Look at my week" }))
    await waitFor(() => expect(scanForImport).toHaveBeenCalled())
    await screen.findByTestId("qualifying-count")

    // Advance to Review via the wizard's own Continue, now that a scan exists.
    await user.click(screen.getByRole("button", { name: /continue/i }))
    await screen.findByText("Which of these are clients?")

    // "Jane Miller" was preselected (confidence 0.9); "Standup" was not
    // (confidence 0.4) — confirming must carry only the one still checked.
    expect(screen.getByText("Jane Miller")).toBeInTheDocument()
    expect(screen.getByRole("checkbox", { name: "Standup" })).not.toBeChecked()

    await user.click(screen.getByRole("button", { name: /add 1 client/i }))

    await waitFor(() => expect(confirmImport).toHaveBeenCalled())
    const [series] = confirmImport.mock.calls[0] as unknown as [Array<{ candidate_key: string }>]
    expect(series.map((item) => item.candidate_key)).toEqual(["a"])
    expect(await screen.findByText(/1 client added/i)).toBeInTheDocument()
  })

  it("asks for incremental import consent before it can scan", async () => {
    getStatus.mockResolvedValue(CONNECTED)
    scanForImport.mockResolvedValue({
      needs_consent: true,
      capability: "import",
      auth_url: "https://accounts.google.com/o/oauth2/auth?scope=readonly",
    })
    const user = userEvent.setup()
    renderWizard()
    await goToClientsStep(user)

    await user.click(screen.getByRole("button", { name: "Look at my week" }))

    await waitFor(() =>
      expect(window.location.assign).toHaveBeenCalledWith(
        "https://accounts.google.com/o/oauth2/auth?scope=readonly"
      )
    )
    expect(window.sessionStorage.getItem("pablo.calendar-import.pending")).toBe("1")
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

  it("completes an incremental import grant and finishes what 'Look at my week' started", async () => {
    getStatus.mockResolvedValue(CONNECTED)
    completeImportConsent.mockResolvedValue({ status: "connected" })
    scanForImport.mockResolvedValue(proposalWith())
    getBusyWindows.mockResolvedValue({ windows: [] })
    window.sessionStorage.setItem("pablo.calendar-import.pending", "1")

    renderWizard()

    await waitFor(() => expect(completeImportConsent).toHaveBeenCalled())
    const [code, state, redirectUri] = completeImportConsent.mock.calls[0]
    expect(code).toBe("auth-code")
    expect(state).toBe("state-from-google")
    expect(redirectUri).toBe("https://app.example.test/dashboard/settings/calendar")

    // The scan the button asked for runs automatically once the grant lands
    // — the therapist never has to press it a second time.
    await waitFor(() => expect(scanForImport).toHaveBeenCalled())
    await screen.findByText("Bring over your week")
    await screen.findByTestId("qualifying-count")

    expect(window.sessionStorage.getItem("pablo.calendar-import.pending")).toBeNull()
    // Never mistaken for a fresh connect.
    expect(completeConnect).not.toHaveBeenCalled()
  })
})
