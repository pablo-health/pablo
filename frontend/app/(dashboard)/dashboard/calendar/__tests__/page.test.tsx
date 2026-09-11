// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import CalendarPage from "../page"

// Mutable so a test can flip the deployment toggle; hoisted because vi.mock
// factories run before the module body.
const runtimeConfig = vi.hoisted(() => ({ googleCalendarEnabled: false }))
vi.mock("@/lib/config", () => ({
  useConfig: () => runtimeConfig,
}))

const preferencesState = vi.hoisted(() => ({
  data: undefined as Record<string, unknown> | undefined,
}))
const savePreferences = vi.hoisted(() => vi.fn())
vi.mock("@/hooks/usePreferences", () => ({
  usePreferences: () => ({ data: preferencesState.data }),
  useSavePreferences: () => ({ mutate: savePreferences, isPending: false }),
}))
// Mutable so a test can put the practice on either side of the "no
// availability rules at all" gate.
const rulesState = vi.hoisted(() => ({
  data: undefined as { data: unknown[]; total: number } | undefined,
}))
vi.mock("@/hooks/useAvailability", () => ({
  useAvailabilityRules: () => ({ data: rulesState.data }),
}))

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ loading: false }),
}))
vi.mock("@/components/theme/ThemeProvider", () => ({
  useTheme: () => ({ theme: "warm-paper" }),
}))
vi.mock("@/lib/access/readOnlyMode", () => ({
  useReadOnlyMode: () => ({ readOnly: false }),
}))
vi.mock("@/lib/api/scheduling", () => ({
  getICalSyncStatus: vi.fn().mockResolvedValue({ connections: [] }),
  triggerICalSync: vi.fn(),
}))

// The calendar and the sheet are their own components with their own
// tests; here they only need to be tellable apart from the wizard.
vi.mock("@/components/calendar/editorial", () => ({
  EditorialCalendar: () => <div data-testid="editorial-calendar" />,
}))
vi.mock("@/components/calendar/AppointmentModal", () => ({
  AppointmentModal: () => null,
}))
vi.mock("@/components/calendar/connect/CalendarHoursStep", () => ({
  CalendarHoursStep: ({ onSaved, onSkip }: { onSaved: () => void; onSkip: () => void }) => (
    <div data-testid="calendar-hours-step">
      <button onClick={onSaved}>Save hours</button>
      <button onClick={onSkip}>Skip hours</button>
    </div>
  ),
}))
vi.mock("@/components/calendar/connect/CalendarSetupWizard", () => ({
  CalendarSetupWizard: ({
    returnPath,
    onFinishLater,
    onDone,
    withHoursStep,
  }: {
    returnPath?: string
    onFinishLater?: () => void
    onDone?: () => void
    withHoursStep?: boolean
  }) => (
    <div
      data-testid="calendar-setup-wizard"
      data-return-path={returnPath}
      data-with-hours-step={String(Boolean(withHoursStep))}
    >
      <button onClick={onFinishLater}>Finish later</button>
      <button onClick={onDone}>Done</button>
    </div>
  ),
}))

const WORKING_HOURS_RULE = {
  id: "rule-1",
  rule_type: "working_hours",
  params: { day_of_week: 0, start: "09:00", end: "17:00" },
}

const PREFERENCES = {
  default_video_platform: "zoom",
  default_session_type: "individual",
  default_duration_minutes: 50,
  auto_transcribe: true,
  quality_preset: "balanced",
  therapist_display_name: null,
  calendar_default_view: "timeGridWeek",
  timezone: "America/New_York",
  theme: "warm-paper",
  calendar_density: "balanced",
}

describe("CalendarPage first visit", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    runtimeConfig.googleCalendarEnabled = true
    preferencesState.data = { ...PREFERENCES }
    rulesState.data = { data: [WORKING_HOURS_RULE], total: 1 }
  })

  it("opens on the setup wizard until it has been finished or waved away", () => {
    render(<CalendarPage />)

    expect(screen.getByTestId("calendar-setup-wizard")).toBeInTheDocument()
    expect(screen.queryByTestId("editorial-calendar")).not.toBeInTheDocument()
  })

  it("keeps the wizard on this page for the round trip to Google", () => {
    render(<CalendarPage />)

    // Google must land the browser back where the wizard is, not on
    // Settings — that is what makes this the in-surface flow.
    expect(screen.getByTestId("calendar-setup-wizard")).toHaveAttribute(
      "data-return-path",
      "/dashboard/calendar"
    )
  })

  it("shows the calendar once setup has been marked complete", () => {
    preferencesState.data = { ...PREFERENCES, calendar_setup_complete: true }

    render(<CalendarPage />)

    expect(screen.getByTestId("editorial-calendar")).toBeInTheDocument()
    expect(screen.queryByTestId("calendar-setup-wizard")).not.toBeInTheDocument()
  })

  it("records 'Finish later' so the wizard does not come back every visit", async () => {
    const user = userEvent.setup()
    render(<CalendarPage />)

    await user.click(screen.getByRole("button", { name: "Finish later" }))

    expect(savePreferences).toHaveBeenCalledWith({ ...PREFERENCES, calendar_setup_complete: true })
  })

  it("records finishing the wizard the same way", async () => {
    const user = userEvent.setup()
    render(<CalendarPage />)

    await user.click(screen.getByRole("button", { name: "Done" }))

    expect(savePreferences).toHaveBeenCalledWith({ ...PREFERENCES, calendar_setup_complete: true })
  })

  it("never shows the wizard on a deployment that has not enabled calendar", () => {
    runtimeConfig.googleCalendarEnabled = false

    render(<CalendarPage />)

    expect(screen.getByTestId("editorial-calendar")).toBeInTheDocument()
    expect(screen.queryByTestId("calendar-setup-wizard")).not.toBeInTheDocument()
  })

  it("waits for preferences before deciding, rather than flashing the wizard", () => {
    preferencesState.data = undefined

    render(<CalendarPage />)

    expect(screen.queryByTestId("calendar-setup-wizard")).not.toBeInTheDocument()
    expect(screen.queryByTestId("editorial-calendar")).not.toBeInTheDocument()
  })

  it("waits for the rule list before deciding, rather than flashing a step", () => {
    rulesState.data = undefined

    render(<CalendarPage />)

    expect(screen.queryByTestId("calendar-hours-step")).not.toBeInTheDocument()
    expect(screen.queryByTestId("calendar-setup-wizard")).not.toBeInTheDocument()
    expect(screen.queryByTestId("editorial-calendar")).not.toBeInTheDocument()
  })
})

describe("CalendarPage hours capture", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    runtimeConfig.googleCalendarEnabled = false
    preferencesState.data = { ...PREFERENCES, calendar_setup_complete: true }
    rulesState.data = { data: [], total: 0 }
  })

  it("opens on the hours capture when the practice has no availability rules", () => {
    render(<CalendarPage />)

    expect(screen.getByTestId("calendar-hours-step")).toBeInTheDocument()
    expect(screen.queryByTestId("editorial-calendar")).not.toBeInTheDocument()
  })

  it("never shows it once any rule exists", () => {
    rulesState.data = { data: [WORKING_HOURS_RULE], total: 1 }

    render(<CalendarPage />)

    expect(screen.queryByTestId("calendar-hours-step")).not.toBeInTheDocument()
    expect(screen.getByTestId("editorial-calendar")).toBeInTheDocument()
  })

  it("asks for hours even on a deployment with no Google Calendar at all", () => {
    runtimeConfig.googleCalendarEnabled = false

    render(<CalendarPage />)

    expect(screen.getByTestId("calendar-hours-step")).toBeInTheDocument()
  })

  it("hands the hours step to the wizard as its first step when Google setup is also due", () => {
    runtimeConfig.googleCalendarEnabled = true
    preferencesState.data = { ...PREFERENCES }

    render(<CalendarPage />)

    expect(screen.getByTestId("calendar-setup-wizard")).toHaveAttribute(
      "data-with-hours-step",
      "true"
    )
    expect(screen.queryByTestId("calendar-hours-step")).not.toBeInTheDocument()
  })

  it("leaves the wizard's own gate alone when the practice already has rules", () => {
    runtimeConfig.googleCalendarEnabled = true
    preferencesState.data = { ...PREFERENCES }
    rulesState.data = { data: [WORKING_HOURS_RULE], total: 1 }

    render(<CalendarPage />)

    expect(screen.getByTestId("calendar-setup-wizard")).toHaveAttribute(
      "data-with-hours-step",
      "false"
    )
  })

  it("skipping shows the calendar without recording anything", async () => {
    const user = userEvent.setup()
    render(<CalendarPage />)

    await user.click(screen.getByRole("button", { name: "Skip hours" }))

    expect(screen.getByTestId("editorial-calendar")).toBeInTheDocument()
    expect(savePreferences).not.toHaveBeenCalled()
  })

  it("saving does not mark the Google setup complete either", async () => {
    const user = userEvent.setup()
    render(<CalendarPage />)

    await user.click(screen.getByRole("button", { name: "Save hours" }))

    expect(savePreferences).not.toHaveBeenCalled()
  })
})
