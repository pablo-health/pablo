// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
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

// Signed in and settled unless a test says otherwise — the state the wizard
// runs in for every case except the boot race the exchange has to survive.
const SIGNED_IN = {
  user: { uid: "u1", email: "t@example.test", displayName: null, photoURL: null },
  loading: false,
}
let authState: { user: { uid: string } | null; loading: boolean } = SIGNED_IN

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ ...authState, getIdToken: async () => "token" }),
}))

// Outer hooks run before the nested ones, so a test that wants the boot
// race sets `authState` in its own body and this puts it back afterwards.
beforeEach(() => {
  authState = SIGNED_IN
})

const getStatus = vi.fn<() => Promise<GoogleCalendarStatus>>()
const getConsentOptions = vi.fn<() => Promise<GoogleCalendarConsentOptions>>()
const getAuthUrl = vi.fn()
const completeConnect = vi.fn()
const disconnect = vi.fn()
const setTitling = vi.fn()
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
  setGoogleCalendarEventTitling: (...args: unknown[]) => setTitling(...args),
    getCalendarBusyWindows: (...args: unknown[]) => getBusyWindows(...args),
    scanCalendarForImport: (...args: unknown[]) => scanForImport(...args),
    completeGoogleCalendarImportConsent: (...args: unknown[]) => completeImportConsent(...args),
    confirmCalendarImport: (...args: unknown[]) => confirmImport(...args),
  }
})

// The hours step has its own tests; here it only needs to save or skip.
vi.mock("../CalendarHoursStep", () => ({
  CalendarHoursStep: ({ onSaved, onSkip }: { onSaved: () => void; onSkip: () => void }) => (
    <div data-testid="calendar-hours-step">
      <button onClick={onSaved}>Save hours</button>
      <button onClick={onSkip}>Skip hours</button>
    </div>
  ),
}))

const DISCONNECTED: GoogleCalendarStatus = {
  connected: false,
  calendar_id: null,
  calendar_name: null,
  last_synced_at: null,
  write_target: null,
  event_titling: null,
  titling_needs_attestation: false,
}

const CONSENT_OPTIONS: GoogleCalendarConsentOptions = {
  write_targets: [
    { id: "app_calendar", promise: "Google Calendar limits access to the calendar Pablo creates." },
    {
      id: "primary",
      promise:
        "Google Calendar grants broader access. Pablo uses it only for adding, updating and removing sessions booked in Pablo.",
    },
  ],
  busy: {
    id: "busy",
    promise:
      "Google Calendar limits access to busy times; event titles and guests are not shared.",
  },
  default_write_target: "app_calendar",
  busy_default: true,
}

function renderWizard(props: React.ComponentProps<typeof CalendarSetupWizard> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <CalendarSetupWizard {...props} />
    </QueryClientProvider>
  )
}

async function goToSessionsStep(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /session calendar/i }))
  await screen.findByText("Choose a calendar")
}

async function goToClientsStep(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /your clients/i }))
  await screen.findByText("Import recurring sessions")
}

const CONNECTED: GoogleCalendarStatus = {
  connected: true,
  calendar_id: "pablo-made@group.calendar.google.com",
  calendar_name: "Pablo Sessions",
  last_synced_at: null,
  write_target: "app_calendar",
  event_titling: null,
  titling_needs_attestation: false,
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
        source_identifier: "series:rec-a",
        match: { patient: null, possible: [], suggested_patient_id: null },
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
        source_identifier: "series:rec-b",
        match: { patient: null, possible: [], suggested_patient_id: null },
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

    await user.click(await screen.findByRole("button", { name: "Continue with Google" }))

    await waitFor(() => expect(getAuthUrl).toHaveBeenCalled())
    expect(getAuthUrl.mock.calls[0][1]).toEqual({ write_target: "app_calendar", busy: true, event_titling: "initials" })
    expect(window.location.assign).toHaveBeenCalledWith(
      "https://accounts.google.com/o/oauth2/auth?x=1"
    )
  })

  it("a connect is never mistaken for an abandoned 'Scan calendar' grant", async () => {
    // The consent for the import was abandoned at Google; its marker stayed.
    window.sessionStorage.setItem("pablo.calendar-import.pending", "1")
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole("button", { name: "Continue with Google" }))

    await waitFor(() => expect(getAuthUrl).toHaveBeenCalled())
    expect(window.sessionStorage.getItem("pablo.calendar-import.pending")).toBeNull()
  })

  it("asks for the main calendar only when that is chosen", async () => {
    const user = userEvent.setup()
    renderWizard()
    await goToSessionsStep(user)

    await user.click(screen.getByRole("radio", { name: /my main calendar/i }))
    await user.click(screen.getByRole("button", { name: "Continue with Google" }))

    await waitFor(() => expect(getAuthUrl).toHaveBeenCalled())
    expect(getAuthUrl.mock.calls[0][1]).toEqual({ write_target: "primary", busy: true, event_titling: "initials" })
  })

  it("does not ask for busy times when that is unchecked", async () => {
    const user = userEvent.setup()
    renderWizard()
    await goToSessionsStep(user)

    await user.click(screen.getByRole("checkbox", { name: "Check for scheduling conflicts" }))
    await user.click(screen.getByRole("button", { name: "Continue with Google" }))

    await waitFor(() => expect(getAuthUrl).toHaveBeenCalled())
    expect(getAuthUrl.mock.calls[0][1]).toEqual({ write_target: "app_calendar", busy: false, event_titling: "initials" })
  })

  it("shows each choice's promise as the API generated it, behind its info button", async () => {
    const user = userEvent.setup()
    renderWizard()
    await goToSessionsStep(user)

    const [appCalendar, primary, busy] = screen.getAllByRole("button", {
      name: "About this permission",
    })
    expect(
      screen.queryByText("Google Calendar limits access to the calendar Pablo creates.")
    ).not.toBeInTheDocument()

    await user.click(appCalendar)
    expect(
      screen.getByText("Google Calendar limits access to the calendar Pablo creates.")
    ).toBeInTheDocument()
    await user.keyboard("{Escape}")

    await user.click(primary)
    expect(
      screen.getByText(
        "Google Calendar grants broader access. Pablo uses it only for adding, updating and removing sessions booked in Pablo."
      )
    ).toBeInTheDocument()
    await user.keyboard("{Escape}")

    await user.click(busy)
    expect(
      screen.getByText(
        "Google Calendar limits access to busy times; event titles and guests are not shared."
      )
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
      calendar_name: "Pablo Sessions",
      last_synced_at: null,
      write_target: "app_calendar",
      event_titling: null,
      titling_needs_attestation: false,
    })
    disconnect.mockResolvedValue({ status: "disconnected" })
    const user = userEvent.setup()
    renderWizard()

    // The name, not the id. A Pablo-made calendar's id is an opaque
    // ...@group.calendar.google.com hash, and showing it to the therapist
    // says nothing — this test used to assert the hash was on screen.
    expect(await screen.findByText("Pablo Sessions")).toBeInTheDocument()
    expect(screen.queryByText("pablo-made@group.calendar.google.com")).not.toBeInTheDocument()
    expect(screen.getByText("A separate calendar for Pablo sessions")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /disconnect/i }))
    // Nothing happens until the clinician confirms.
    const dialog = await screen.findByRole("dialog", { name: "Disconnect Google Calendar?" })
    expect(disconnect).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }))

    await waitFor(() => expect(disconnect).toHaveBeenCalledTimes(1))
  })

  it("keeps the connection when the disconnect is cancelled", async () => {
    getStatus.mockResolvedValue({
      connected: true,
      calendar_id: "pablo-made@group.calendar.google.com",
      calendar_name: "Pablo Sessions",
      last_synced_at: null,
      write_target: "app_calendar",
      event_titling: null,
      titling_needs_attestation: false,
    })
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole("button", { name: /disconnect/i }))
    const dialog = await screen.findByRole("dialog", { name: "Disconnect Google Calendar?" })
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
    expect(disconnect).not.toHaveBeenCalled()
  })

  it("surfaces a failure to start the connection instead of leaving a dead button", async () => {
    getAuthUrl.mockRejectedValue(new Error("Google is unreachable"))
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole("button", { name: "Continue with Google" }))

    expect(await screen.findByText("Google is unreachable")).toBeInTheDocument()
  })

  it("skipping the week completes the wizard without ever scanning", async () => {
    getStatus.mockResolvedValue(CONNECTED)
    const user = userEvent.setup()
    renderWizard()
    await goToClientsStep(user)

    await user.click(screen.getByRole("button", { name: "Skip import" }))

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
      already_scheduled: [],
    })
    const user = userEvent.setup()
    renderWizard()
    await goToClientsStep(user)

    await user.click(screen.getByRole("button", { name: "Scan calendar" }))
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

  it("confirms each series onto the client it matched, or as a new client", async () => {
    getStatus.mockResolvedValue(CONNECTED)
    const base = proposalWith()
    scanForImport.mockResolvedValue({
      ...base,
      series: [
        {
          ...base.series[0],
          match: {
            patient: { patient_id: "p-1", display_name: "Jane Miller", date_of_birth: null },
            possible: [],
            suggested_patient_id: null,
          },
        },
        {
          ...base.series[1],
          summary: "Sam Lee",
          preselected: true,
          match: {
            patient: null,
            possible: [
              { patient_id: "p-7", display_name: "Sam Lee", date_of_birth: "1990-03-14" },
              { patient_id: "p-8", display_name: "Sam Lee", date_of_birth: null },
            ],
            suggested_patient_id: null,
          },
        },
      ],
    })
    confirmImport.mockResolvedValue({
      confirmed: [],
      patients_created: 0,
      appointments_created: 0,
      skipped: [],
      already_scheduled: [],
    })
    const user = userEvent.setup()
    renderWizard()
    await goToClientsStep(user)

    await user.click(screen.getByRole("button", { name: "Scan calendar" }))
    await screen.findByTestId("qualifying-count")
    await user.click(screen.getByRole("button", { name: /continue/i }))
    await screen.findByText("Matches Jane Miller")

    await user.selectOptions(
      screen.getByRole("combobox", { name: "Which client is Sam Lee?" }),
      "p-7"
    )
    await user.click(screen.getByRole("button", { name: /add 2 clients/i }))

    await waitFor(() => expect(confirmImport).toHaveBeenCalled())
    const [series] = confirmImport.mock.calls[0] as unknown as [
      Array<{ candidate_key: string; patient_id: string | null; source_identifier: string }>,
    ]
    expect(
      series.map(({ candidate_key, patient_id, source_identifier }) => ({
        candidate_key,
        patient_id,
        source_identifier,
      }))
    ).toEqual([
      { candidate_key: "a", patient_id: "p-1", source_identifier: "series:rec-a" },
      { candidate_key: "b", patient_id: "p-7", source_identifier: "series:rec-b" },
    ])
  })

  it("sends a new client's name as typed or as filled in", async () => {
    getStatus.mockResolvedValue(CONNECTED)
    const base = proposalWith()
    scanForImport.mockResolvedValue({
      ...base,
      series: [
        {
          ...base.series[0],
          summary: "K.M.",
          preselected: true,
          suggested_name: null,
        },
        {
          ...base.series[1],
          summary: "Session with Casey Morgan",
          preselected: true,
          suggested_name: { first_name: "Casey", last_name: "Morgan" },
        },
      ],
    })
    confirmImport.mockResolvedValue({
      confirmed: [],
      patients_created: 2,
      appointments_created: 0,
      skipped: [],
      already_scheduled: [],
    })
    const user = userEvent.setup()
    renderWizard()
    await goToClientsStep(user)

    await user.click(screen.getByRole("button", { name: "Scan calendar" }))
    await screen.findByTestId("qualifying-count")
    await user.click(screen.getByRole("button", { name: /continue/i }))

    const initials = await screen.findByTestId("new-client-name-a")
    await user.type(within(initials).getByLabelText("First name"), "Kim")
    await user.type(within(initials).getByLabelText("Last name"), "Moreau")
    await user.click(screen.getByRole("button", { name: /add 2 clients/i }))

    await waitFor(() => expect(confirmImport).toHaveBeenCalled())
    const [series] = confirmImport.mock.calls[0] as unknown as [
      Array<{ candidate_key: string; new_client_first_name: string; new_client_last_name: string }>,
    ]
    expect(
      series.map(({ candidate_key, new_client_first_name, new_client_last_name }) => ({
        candidate_key,
        new_client_first_name,
        new_client_last_name,
      }))
    ).toEqual([
      { candidate_key: "a", new_client_first_name: "Kim", new_client_last_name: "Moreau" },
      { candidate_key: "b", new_client_first_name: "Casey", new_client_last_name: "Morgan" },
    ])
  })

  it("offers a name-only match as a choice, preselected, rather than as settled", async () => {
    getStatus.mockResolvedValue(CONNECTED)
    const base = proposalWith()
    scanForImport.mockResolvedValue({
      ...base,
      series: [
        {
          ...base.series[0],
          match: {
            patient: null,
            possible: [{ patient_id: "p-1", display_name: "Jane Miller", date_of_birth: null }],
            suggested_patient_id: "p-1",
          },
        },
      ],
    })
    confirmImport.mockResolvedValue({
      confirmed: [],
      patients_created: 0,
      appointments_created: 0,
      skipped: [],
      already_scheduled: [],
    })
    const user = userEvent.setup()
    renderWizard()
    await goToClientsStep(user)

    await user.click(screen.getByRole("button", { name: "Scan calendar" }))
    await screen.findByTestId("qualifying-count")
    await user.click(screen.getByRole("button", { name: /continue/i }))
    await screen.findByText("Which of these are clients?")

    expect(screen.queryByText("Matches Jane Miller")).not.toBeInTheDocument()
    const choice = screen.getByRole("combobox", { name: "Which client is Jane Miller?" })
    expect(choice).toHaveValue("p-1")

    await user.click(screen.getByRole("button", { name: /add 1 client/i }))
    await waitFor(() => expect(confirmImport).toHaveBeenCalled())
    const [series] = confirmImport.mock.calls[0] as unknown as [
      Array<{ candidate_key: string; patient_id: string | null }>,
    ]
    expect(series.map(({ candidate_key, patient_id }) => ({ candidate_key, patient_id }))).toEqual(
      [{ candidate_key: "a", patient_id: "p-1" }]
    )
  })

  it("sends a series marked not a client to be remembered, not imported", async () => {
    getStatus.mockResolvedValue(CONNECTED)
    const base = proposalWith()
    scanForImport.mockResolvedValue({
      ...base,
      series: base.series.map((item) => ({ ...item, preselected: true })),
    })
    confirmImport.mockResolvedValue({
      confirmed: [],
      patients_created: 1,
      appointments_created: 4,
      skipped: [],
      already_scheduled: [],
    })
    const user = userEvent.setup()
    renderWizard()
    await goToClientsStep(user)

    await user.click(screen.getByRole("button", { name: "Scan calendar" }))
    await screen.findByTestId("qualifying-count")
    await user.click(screen.getByRole("button", { name: /continue/i }))
    await screen.findByText("Which of these are clients?")

    // Rows render in order; the second is "Standup".
    await user.click(screen.getAllByRole("button", { name: "Not a client" })[1])
    await user.click(screen.getByRole("button", { name: /add 1 client/i }))

    await waitFor(() => expect(confirmImport).toHaveBeenCalled())
    const [series, notClients] = confirmImport.mock.calls[0] as unknown as [
      Array<{ candidate_key: string }>,
      string[],
    ]
    expect(series.map((item) => item.candidate_key)).toEqual(["a"])
    expect(notClients).toEqual(["series:rec-b"])
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

    await user.click(screen.getByRole("button", { name: "Scan calendar" }))

    await waitFor(() =>
      expect(window.location.assign).toHaveBeenCalledWith(
        "https://accounts.google.com/o/oauth2/auth?scope=readonly"
      )
    )
    expect(window.sessionStorage.getItem("pablo.calendar-import.pending")).toBe("1")
  })
})

describe("CalendarSetupWizard event titling", () => {
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

  it("shows what each choice makes an event actually say", async () => {
    const user = userEvent.setup()
    renderWizard()
    await goToSessionsStep(user)

    expect(screen.getByText(/Therapy Session · 3:00–3:50 PM/)).toBeInTheDocument()
    expect(screen.getByText(/J\.M\. · 3:00–3:50 PM/)).toBeInTheDocument()
    expect(screen.getByText(/Jane Miller · 3:00–3:50 PM/)).toBeInTheDocument()
  })

  it("defaults a new connection to initials", async () => {
    const user = userEvent.setup()
    renderWizard()
    await goToSessionsStep(user)

    expect(screen.getByRole("radio", { name: /initials/i })).toBeChecked()

    await user.click(screen.getByRole("button", { name: "Continue with Google" }))
    await waitFor(() => expect(getAuthUrl).toHaveBeenCalled())
    expect(getAuthUrl.mock.calls[0][1].event_titling).toBe("initials")
  })

  it("carries the chosen wording through to the connect", async () => {
    const user = userEvent.setup()
    renderWizard()
    await goToSessionsStep(user)

    await user.click(screen.getByRole("radio", { name: /therapy session/i }))
    await user.click(screen.getByRole("button", { name: "Continue with Google" }))

    await waitFor(() => expect(getAuthUrl).toHaveBeenCalled())
    expect(getAuthUrl.mock.calls[0][1].event_titling).toBe("generic")
  })

  it("asks for a confirmation before full names, and blocks Finish until given", async () => {
    getStatus.mockResolvedValue({
      connected: true,
      calendar_id: "jane@example.test",
      calendar_name: "Pablo Sessions",
      last_synced_at: null,
      write_target: "app_calendar",
      event_titling: "initials",
      titling_needs_attestation: false,
    })
    const user = userEvent.setup()
    renderWizard()
    await goToSessionsStep(user)

    await user.click(screen.getByRole("radio", { name: /^full name/i }))

    const attestation = screen.getByRole("checkbox", {
      name: /covered by my practice’s business associate agreement with Google/i,
    })
    expect(attestation).toBeInTheDocument()
    // A condition of the choice, so on the page rather than behind a button.
    expect(
      screen.getByText("Personal Gmail accounts cannot be used for full names.")
    ).toBeVisible()
    // The wizard's own nav button, not the step's connect action.
    const nav = () => screen.getAllByRole("button", { name: /continue|finish/i }).at(-1)!
    expect(nav()).toBeDisabled()

    await user.click(attestation)
    expect(nav()).not.toBeDisabled()
  })

  it("says so when the full-name choice was confirmed for another account", async () => {
    getStatus.mockResolvedValue({
      connected: true,
      calendar_id: "new-account@example.test",
      calendar_name: "Pablo Sessions",
      last_synced_at: null,
      write_target: "app_calendar",
      event_titling: "initials",
      titling_needs_attestation: true,
    })
    const user = userEvent.setup()
    renderWizard()
    await goToSessionsStep(user)

    expect(screen.getByText(/chose full names for a different Google account/i)).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: /initials/i })).toBeChecked()
  })

  it("saves a change on an already-connected calendar without asking Google again", async () => {
    getStatus.mockResolvedValue({
      connected: true,
      calendar_id: "jane@example.test",
      calendar_name: "Pablo Sessions",
      last_synced_at: null,
      write_target: "app_calendar",
      event_titling: "initials",
      titling_needs_attestation: false,
    })
    setTitling.mockResolvedValue({ style: "generic", events_retitled: 3, events_not_retitled: 0 })
    const user = userEvent.setup()
    renderWizard()
    await goToSessionsStep(user)

    await user.click(screen.getByRole("radio", { name: /therapy session/i }))
    expect(screen.queryByRole("button", { name: "Continue with Google" })).toBeNull()
    await user.click(screen.getByRole("button", { name: "Save event titles" }))

    await waitFor(() => expect(setTitling).toHaveBeenCalledWith("generic", false))
    expect(getAuthUrl).not.toHaveBeenCalled()
  })
})

describe("CalendarSetupWizard changing an existing connection", () => {
  const CONNECTED_WITH_BUSY: GoogleCalendarStatus = {
    connected: true,
    calendar_id: "pablo-made@group.calendar.google.com",
    calendar_name: "Pablo Sessions",
    last_synced_at: null,
    write_target: "app_calendar",
    busy: true,
    event_titling: "initials",
    titling_needs_attestation: false,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    window.sessionStorage.clear()
    getStatus.mockResolvedValue(CONNECTED_WITH_BUSY)
    getConsentOptions.mockResolvedValue(CONSENT_OPTIONS)
    getAuthUrl.mockResolvedValue({ auth_url: "https://accounts.google.com/o/oauth2/auth?x=1" })
    getBusyWindows.mockResolvedValue({ windows: [] })
  })

  it("offers nothing to press when nothing has changed", async () => {
    const user = userEvent.setup()
    renderWizard()
    await goToSessionsStep(user)

    expect(screen.queryByRole("button", { name: "Continue with Google" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Save event titles" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Continue with Google" })).toBeNull()
  })

  it("goes to Google when where sessions go changes", async () => {
    const user = userEvent.setup()
    renderWizard()
    await goToSessionsStep(user)

    await user.click(screen.getByRole("radio", { name: /my main calendar/i }))
    expect(screen.getByText("Google will ask you to approve the updated access.")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Continue with Google" }))

    await waitFor(() => expect(getAuthUrl).toHaveBeenCalled())
    expect(getAuthUrl.mock.calls[0][1].write_target).toBe("primary")
    expect(setTitling).not.toHaveBeenCalled()
  })

  it("goes to Google when busy times are turned off", async () => {
    const user = userEvent.setup()
    renderWizard()
    await goToSessionsStep(user)

    await user.click(screen.getByRole("checkbox", { name: "Check for scheduling conflicts" }))
    await user.click(screen.getByRole("button", { name: "Continue with Google" }))

    await waitFor(() => expect(getAuthUrl).toHaveBeenCalled())
    expect(getAuthUrl.mock.calls[0][1].busy).toBe(false)
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
      JSON.stringify({ write_target: "primary", busy: false, event_titling: "generic" })
    )

    renderWizard()

    await waitFor(() => expect(completeConnect).toHaveBeenCalled())
    const [code, state, redirectUri, selection] = completeConnect.mock.calls[0]
    expect(code).toBe("auth-code")
    // The backend checks this was minted for the signed-in user, so it has
    // to survive the round trip rather than being dropped here.
    expect(state).toBe("state-from-google")
    expect(redirectUri).toBe("https://app.example.test/dashboard/settings/calendar")
    expect(selection).toEqual({
      write_target: "primary",
      busy: false,
      event_titling: "generic",
    })
    // The one-time code must not survive a refresh.
    await waitFor(() =>
      expect(routerReplace).toHaveBeenCalledWith("/dashboard/settings/calendar")
    )
  })

  it("waits for auth before spending the code, then spends it once auth arrives", async () => {
    // Coming back from Google is a full page load, and this component's
    // effects run before the auth provider's. Exchanging here would send an
    // unauthenticated request, and the code only gets one attempt.
    authState = { user: null, loading: true }
    const { rerender } = renderWizard()

    // Mounted, and holding everything until sign-in settles: the exchange,
    // and the status read that would otherwise go out with no token.
    await screen.findByRole("button", { name: "Continue with Google" })
    expect(getStatus).not.toHaveBeenCalled()
    expect(completeConnect).not.toHaveBeenCalled()
    // Scrubbing the code now would strip it before anyone could spend it.
    expect(routerReplace).not.toHaveBeenCalled()

    authState = SIGNED_IN
    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <CalendarSetupWizard />
      </QueryClientProvider>
    )

    await waitFor(() => expect(completeConnect).toHaveBeenCalledTimes(1))
    expect(completeConnect.mock.calls[0][0]).toBe("auth-code")
    await waitFor(() => expect(getStatus).toHaveBeenCalled())
  })

  it("leaves the code alone when auth settles signed out", async () => {
    authState = { user: null, loading: false }

    renderWizard()

    await waitFor(() => expect(getStatus).toHaveBeenCalled())
    expect(completeConnect).not.toHaveBeenCalled()
    expect(routerReplace).not.toHaveBeenCalled()
  })

  it("completes an incremental import grant and finishes what 'Scan calendar' started", async () => {
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
    // Read in the therapist's own zone, never left to the scan's UTC default.
    expect(scanForImport.mock.calls[0][1]).toBe(
      Intl.DateTimeFormat().resolvedOptions().timeZone
    )
    await screen.findByText("Import recurring sessions")
    await screen.findByTestId("qualifying-count")

    expect(window.sessionStorage.getItem("pablo.calendar-import.pending")).toBeNull()
    // Never mistaken for a fresh connect.
    expect(completeConnect).not.toHaveBeenCalled()
  })

  it("lands on Sessions after a connect, and says it is connected", async () => {
    getStatus.mockResolvedValue(CONNECTED)
    getBusyWindows.mockResolvedValue({ windows: [] })

    renderWizard()

    await waitFor(() => expect(completeConnect).toHaveBeenCalled())
    await screen.findByText("Choose a calendar")
    expect(screen.getByRole("status")).toHaveTextContent("Google Calendar is connected.")
  })

  it("reports a failed import grant on the clients step, not the first one", async () => {
    getStatus.mockResolvedValue(CONNECTED)
    completeImportConsent.mockRejectedValue(new Error("Google did not finish granting access."))
    getBusyWindows.mockResolvedValue({ windows: [] })
    window.sessionStorage.setItem("pablo.calendar-import.pending", "1")

    renderWizard()

    await screen.findByText("Import recurring sessions")
    await screen.findByText(/google did not finish granting access/i)
    expect(scanForImport).not.toHaveBeenCalled()
  })
})

describe("CalendarSetupWizard hosted on another page", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.sessionStorage.clear()
    searchParams.delete("code")
    searchParams.delete("state")
    getStatus.mockResolvedValue(DISCONNECTED)
    getConsentOptions.mockResolvedValue(CONSENT_OPTIONS)
    getAuthUrl.mockResolvedValue({ auth_url: "https://accounts.google.com/o/oauth2/auth?x=1" })
    getBusyWindows.mockResolvedValue({ windows: [] })
    Object.defineProperty(window, "location", {
      value: { origin: "https://app.example.test", assign: vi.fn() },
      writable: true,
    })
  })

  it("sends Google back to the page it is mounted on", async () => {
    const user = userEvent.setup()
    renderWizard({ returnPath: "/dashboard/calendar" })

    await user.click(await screen.findByRole("button", { name: "Continue with Google" }))

    await waitFor(() => expect(getAuthUrl).toHaveBeenCalled())
    expect(getAuthUrl.mock.calls[0][0]).toBe("https://app.example.test/dashboard/calendar")
  })

  it("exchanges the code against that page and scrubs it from there", async () => {
    completeConnect.mockResolvedValue({ status: "connected" })
    searchParams.set("code", "auth-code")
    searchParams.set("state", "state-from-google")

    renderWizard({ returnPath: "/dashboard/calendar" })

    await waitFor(() => expect(completeConnect).toHaveBeenCalled())
    expect(completeConnect.mock.calls[0][2]).toBe("https://app.example.test/dashboard/calendar")
    await waitFor(() => expect(routerReplace).toHaveBeenCalledWith("/dashboard/calendar"))
    expect(routerReplace).not.toHaveBeenCalledWith("/dashboard/settings/calendar")
  })

  it("hands 'Finish later' to the host instead of leaving for Settings", async () => {
    const onFinishLater = vi.fn()
    const user = userEvent.setup()
    renderWizard({ onFinishLater })

    await user.click(await screen.findByRole("button", { name: /finish later/i }))

    expect(onFinishLater).toHaveBeenCalled()
    expect(routerPush).not.toHaveBeenCalled()
  })

  it("hands skipping the week to the host instead of leaving for Settings", async () => {
    getStatus.mockResolvedValue(CONNECTED)
    const onDone = vi.fn()
    const user = userEvent.setup()
    renderWizard({ onDone })
    await goToClientsStep(user)

    await user.click(screen.getByRole("button", { name: "Skip import" }))

    expect(onDone).toHaveBeenCalled()
    expect(routerPush).not.toHaveBeenCalled()
  })

  it("keeps the hours step through a save even once the host stops asking for it", async () => {
    const onHoursAnswered = vi.fn()
    const user = userEvent.setup()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wizard = (withHoursStep: boolean) => (
      <QueryClientProvider client={queryClient}>
        <CalendarSetupWizard withHoursStep={withHoursStep} onHoursAnswered={onHoursAnswered} />
      </QueryClientProvider>
    )
    const { rerender } = render(wizard(true))
    expect(screen.getByTestId("calendar-hours-step")).toBeInTheDocument()

    // The first rule the step creates makes the host's rule list non-empty
    // while the rest are still being written.
    rerender(wizard(false))
    expect(screen.getByTestId("calendar-hours-step")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Save hours" }))

    expect(onHoursAnswered).toHaveBeenCalledTimes(1)
    // Lands on Connect, not one step past it.
    expect(
      await screen.findByRole("heading", { name: "Connect Google Calendar" })
    ).toBeInTheDocument()
    expect(screen.queryByTestId("calendar-hours-step")).not.toBeInTheDocument()
  })

  it("still leaves for Settings when nobody is hosting it", async () => {
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole("button", { name: /finish later/i }))

    expect(routerPush).toHaveBeenCalledWith("/dashboard/settings")
  })
})

/** The stepper's pill for a step, and the number it shows (empty once done). */
function stepperPill(label: string): HTMLElement {
  const pill = screen
    .getAllByRole("button")
    .find((button) => button.textContent?.replace(/^\d+/, "") === label)
  if (!pill) throw new Error(`no stepper pill for ${label}`)
  return pill
}

function stepperNumber(label: string): string {
  return stepperPill(label).textContent?.match(/^\d+/)?.[0] ?? "done"
}

describe("CalendarSetupWizard step numbers", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.sessionStorage.clear()
    searchParams.delete("code")
    searchParams.delete("state")
    getStatus.mockResolvedValue(DISCONNECTED)
    getConsentOptions.mockResolvedValue(CONSENT_OPTIONS)
    completeConnect.mockResolvedValue({ status: "connected" })
    getBusyWindows.mockResolvedValue({ windows: [] })
    Object.defineProperty(window, "location", {
      value: { origin: "https://app.example.test", assign: vi.fn() },
      writable: true,
    })
  })

  it("gives the card the stepper's number, with the hours step in front", async () => {
    const user = userEvent.setup()
    renderWizard({ withHoursStep: true })

    await user.click(screen.getByRole("button", { name: "Save hours" }))

    expect(await screen.findByText("Step 2")).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "Connect Google Calendar" })).toBeInTheDocument()
    expect(stepperNumber("Connect")).toBe("2")
  })

  it("keeps 'Hours' and every number after it through the round trip to Google", async () => {
    searchParams.set("code", "auth-code")
    searchParams.set("state", "state-from-google")
    getStatus.mockResolvedValue(CONNECTED)

    // Back from Google on a fresh page load: the hours now exist.
    renderWizard({ withHoursStep: true, hoursSaved: true })

    await screen.findByText("Choose a calendar")
    expect(screen.getByText("Step 3")).toBeInTheDocument()
    expect(stepperNumber("Session calendar")).toBe("3")
    expect(stepperNumber("Your clients")).toBe("4")
    expect(stepperNumber("Hours")).toBe("done")
    expect(stepperNumber("Connect")).toBe("done")
  })

  it("does not tick the session calendar step off before it has been shown", async () => {
    searchParams.set("code", "auth-code")
    searchParams.set("state", "state-from-google")
    getStatus.mockResolvedValue(CONNECTED)

    renderWizard({ withHoursStep: true, hoursSaved: true })

    await screen.findByText("Choose a calendar")
    expect(stepperNumber("Session calendar")).not.toBe("done")
  })

  it("opens on Connect, with the hours shown as saved, when they already exist", async () => {
    const user = userEvent.setup()
    renderWizard({ withHoursStep: true, hoursSaved: true })

    expect(
      await screen.findByRole("heading", { name: "Connect Google Calendar" })
    ).toBeInTheDocument()
    expect(screen.queryByTestId("calendar-hours-step")).not.toBeInTheDocument()

    await user.click(stepperPill("Hours"))
    expect(screen.getByRole("heading", { name: "Your hours are saved" })).toBeInTheDocument()
    expect(screen.queryByTestId("calendar-hours-step")).not.toBeInTheDocument()
  })

  it("titles the page for the whole setup, not for Google, while it asks for hours", () => {
    renderWizard({ withHoursStep: true })

    expect(screen.getByRole("heading", { name: "Set up your calendar" })).toBeInTheDocument()
    expect(screen.queryByRole("heading", { name: "Google Calendar" })).not.toBeInTheDocument()
  })

  it("keeps the permission detail behind About Google access", async () => {
    const user = userEvent.setup()
    renderWizard()

    await screen.findByRole("heading", { name: "Connect Google Calendar" })
    expect(screen.queryByText(/before you connect/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/permission screen/i)).not.toBeInTheDocument()
    const about = screen.getByRole("button", { name: "About Google access" })
    expect(screen.queryByText(/choose which calendar Pablo can use/)).not.toBeInTheDocument()

    await user.click(about)
    expect(
      screen.getByText(
        "You’ll choose which calendar Pablo can use and whether Pablo can check your busy times."
      )
    ).toBeInTheDocument()
  })
})
