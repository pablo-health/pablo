// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { EditorialCalendar } from "../EditorialCalendar"
import { ToastProvider } from "@/components/ui/Toast"
import type { AppointmentResponse } from "@/types/scheduling"
import type { OutsideQuestion, OutsideSession } from "@/lib/api/outsideSessions"

const APPOINTMENTS: AppointmentResponse[] = []
const OUTSIDE: OutsideSession[] = []
const QUESTIONS: OutsideQuestion[] = []
const updateMutate = vi.fn()
const answerMutateAsync = vi.fn()

vi.mock("@/hooks/useAppointments", () => ({
  useAppointmentList: () => ({ data: { data: APPOINTMENTS } }),
  useUpdateAppointment: () => ({ mutate: updateMutate }),
}))

vi.mock("@/hooks/useOutsideSessions", () => ({
  useOutsideSessions: () => ({ data: { events: OUTSIDE } }),
  useOutsideQuestions: () => ({ data: { count: QUESTIONS.length, questions: QUESTIONS } }),
  useAnswerOutsideSessions: () => ({ mutateAsync: answerMutateAsync }),
}))

vi.mock("@/hooks/usePatients", () => ({
  usePatientList: () => ({
    data: { data: [{ id: "p1", first_name: "Jane", last_name: "Doe" }] },
  }),
}))

vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  )
  Wrapper.displayName = "TestQueryClientWrapper"
  return Wrapper
}

function defaults() {
  return {
    theme: "light" as const,
    onSelectSlot: vi.fn(),
    onSelectAppointment: vi.fn(),
    onCreateNew: vi.fn(),
  }
}

function todayAt(hour: number): Date {
  const start = new Date()
  start.setHours(hour, 0, 0, 0)
  return start
}

function appointment(overrides: Partial<AppointmentResponse> = {}): AppointmentResponse {
  const start = todayAt(10)
  return {
    id: "a1",
    user_id: "u1",
    patient_id: "p1",
    title: "Jane Doe — Individual",
    start_at: start.toISOString(),
    end_at: new Date(start.getTime() + 50 * 60_000).toISOString(),
    duration_minutes: 50,
    status: "confirmed",
    session_type: "individual",
    video_link: null,
    video_platform: null,
    notes: null,
    note_type: "soap",
    recurrence_rule: null,
    recurring_appointment_id: null,
    recurrence_index: null,
    is_exception: false,
    google_event_id: null,
    google_sync_status: null,
    session_id: null,
    created_at: start.toISOString(),
    updated_at: null,
    ...overrides,
  }
}

function drag(element: HTMLElement) {
  fireEvent.pointerDown(element, { clientX: 0, clientY: 0, pointerId: 1, button: 0 })
  fireEvent.pointerMove(window, { clientX: 0, clientY: 54, pointerId: 1 })
  fireEvent.pointerUp(window, { clientX: 0, clientY: 54, pointerId: 1 })
}

// A name alone: offered as a preselected choice, never as settled.
const MATCHED: OutsideQuestion = {
  key: "google_calendar|series:abc",
  source: "google_calendar",
  source_identifier: "series:abc",
  title: "Jane Doe",
  recurring: true,
  sessions: 4,
  next_start_at: todayAt(14).toISOString(),
  match: {
    patient: null,
    possible: [{ patient_id: "p1", display_name: "Jane Doe", date_of_birth: null }],
    suggested_patient_id: "p1",
  },
}

const UNKNOWN: OutsideQuestion = {
  ...MATCHED,
  key: "google_calendar|title:def",
  source_identifier: "title:def",
  title: "R.K.",
  recurring: false,
  sessions: 1,
  match: { patient: null, possible: [], suggested_patient_id: null },
}

beforeEach(() => {
  APPOINTMENTS.length = 0
  OUTSIDE.length = 0
  QUESTIONS.length = 0
  updateMutate.mockReset()
  answerMutateAsync.mockReset()
  answerMutateAsync.mockResolvedValue({
    answered: 1,
    appointments_created: 1,
    appointments: [],
    not_added: [],
  })
})

describe("sessions from the clinician's own calendar", () => {
  it("renders an open one as a read-only block marked 'Who is this?' that can't be dragged", () => {
    const start = todayAt(14)
    OUTSIDE.push({
      id: "o1",
      source: "google_calendar",
      source_identifier: "title:def",
      title: "R.K.",
      start_at: start.toISOString(),
      end_at: new Date(start.getTime() + 50 * 60_000).toISOString(),
    })

    render(<EditorialCalendar {...defaults()} defaultView="day" />, { wrapper: wrap() })

    const block = screen.getByTestId("outside-session")
    expect(within(block).getByText("R.K.")).toBeInTheDocument()
    expect(within(block).getByTestId("outside-session-marker")).toHaveTextContent("Who is this?")
    // Not an appointment: nothing on it reschedules.
    expect(block).not.toHaveAttribute("data-event")
    drag(block)
    expect(updateMutate).not.toHaveBeenCalled()

    fireEvent.click(block)
    const dialog = screen.getByRole("dialog")
    expect(within(dialog).getByText("Who is this?")).toBeInTheDocument()
    expect(within(dialog).getByRole("button", { name: "Start note" })).toBeInTheDocument()
  })

  it("counts the questions in the banner, and Review opens the shared client list", async () => {
    QUESTIONS.push(MATCHED, UNKNOWN)

    render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })

    const line = screen.getByTestId("outside-sessions-line")
    expect(line).toHaveTextContent("2 sessions from your Google Calendar need a client")
    fireEvent.click(within(line).getByRole("button", { name: "Review" }))

    const dialog = screen.getByRole("dialog")
    expect(within(dialog).getByText("Which of these are clients?")).toBeInTheDocument()
    // A name match starts on that chart, beside "New client", and checked:
    // one confirm, but never shown as settled.
    expect(within(dialog).queryByText("Matches Jane Doe")).not.toBeInTheDocument()
    expect(within(dialog).getByRole("combobox", { name: "Which client is Jane Doe?" })).toHaveValue(
      "p1"
    )
    expect(within(dialog).getByRole("checkbox", { name: "Jane Doe" })).toBeChecked()
    // Anyone else starts unchecked, as a new client, with "Not a client" beside it.
    expect(within(dialog).getByRole("checkbox", { name: "R.K." })).not.toBeChecked()
    expect(within(dialog).getByText("New client", { selector: "span" })).toBeInTheDocument()
    expect(within(dialog).getAllByRole("button", { name: "Not a client" })).toHaveLength(2)

    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }))
    expect(answerMutateAsync).toHaveBeenCalledWith([
      {
        source: "google_calendar",
        source_identifier: "series:abc",
        patient_id: "p1",
        new_client_name: null,
        not_a_client: false,
      },
    ])
  })

  it("shows a colleague's client as seen by them, and never answers it", () => {
    QUESTIONS.push(MATCHED, {
      ...UNKNOWN,
      key: "google_calendar|series:theirs",
      source_identifier: "series:theirs",
      title: "Grace Hopper",
      match: { patient: null, possible: [], suggested_patient_id: null, seen_by: ["Dr. Rivera"] },
    })

    render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })
    fireEvent.click(
      within(screen.getByTestId("outside-sessions-line")).getByRole("button", { name: "Review" })
    )

    const dialog = screen.getByRole("dialog")
    expect(
      within(dialog).getByText("Already in your practice, seen by Dr. Rivera.")
    ).toBeInTheDocument()
    expect(
      within(dialog).getByText("Ask Dr. Rivera or your practice owner for access.")
    ).toBeInTheDocument()
    const theirs = within(dialog).getByRole("checkbox", { name: "Grace Hopper" })
    expect(theirs).not.toBeChecked()
    expect(theirs).toBeDisabled()
    // Only the clinician's own client has "Not a client" beside it.
    expect(within(dialog).getAllByRole("button", { name: "Not a client" })).toHaveLength(1)

    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }))
    expect(answerMutateAsync).toHaveBeenCalledWith([
      {
        source: "google_calendar",
        source_identifier: "series:abc",
        patient_id: "p1",
        new_client_name: null,
        not_a_client: false,
      },
    ])
  })

  it("says which sessions weren't added because another appointment was there", async () => {
    QUESTIONS.push(MATCHED)
    const start = new Date(2099, 0, 5, 14)
    answerMutateAsync.mockResolvedValue({
      answered: 1,
      appointments_created: 3,
      appointments: [],
      not_added: [
        { outside_session_id: "o1", client_name: "Jane Doe", start_at: start.toISOString() },
      ],
    })
    render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })
    fireEvent.click(
      within(screen.getByTestId("outside-sessions-line")).getByRole("button", { name: "Review" })
    )

    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }))

    const line = await screen.findByTestId("outside-not-added")
    expect(line).toHaveTextContent(
      "Jane Doe's session on Mon Jan 5 overlaps another appointment, so it wasn't added."
    )
    // Still open, so it can be read, until the clinician is done with it.
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: "Done" })).toBeVisible()
  })

  it("says 1 session in the singular", () => {
    QUESTIONS.push(UNKNOWN)
    render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })
    expect(screen.getByTestId("outside-sessions-line")).toHaveTextContent(
      "1 session from your Google Calendar needs a client"
    )
  })

  it("won't drag a followed appointment, though an ordinary one moves", () => {
    APPOINTMENTS.push(
      appointment({ id: "followed", outside_source: "google_calendar", outside_event_id: "ev1" })
    )
    render(<EditorialCalendar {...defaults()} defaultView="day" />, { wrapper: wrap() })

    drag(document.querySelector('[data-event="1"]') as HTMLElement)
    expect(updateMutate).not.toHaveBeenCalled()
  })

  it("still drags an appointment Pablo booked itself", () => {
    APPOINTMENTS.push(appointment({ id: "own" }))
    render(<EditorialCalendar {...defaults()} defaultView="day" />, { wrapper: wrap() })

    drag(document.querySelector('[data-event="1"]') as HTMLElement)
    expect(updateMutate).toHaveBeenCalledTimes(1)
  })

  it("answers a question about one event for that event alone", () => {
    const start = todayAt(14)
    OUTSIDE.push({
      id: "o1",
      source: "ical:simplepractice",
      source_identifier: "J.A.",
      title: "J.A. Appointment",
      start_at: start.toISOString(),
      end_at: new Date(start.getTime() + 50 * 60_000).toISOString(),
    })
    // Initials fit two clients, so each event is its own question, pre-filled
    // with the last answer.
    QUESTIONS.push({
      key: "ical:simplepractice|J.A.|o1",
      source: "ical:simplepractice",
      source_identifier: "J.A.",
      title: "J.A. Appointment",
      recurring: false,
      sessions: 1,
      next_start_at: start.toISOString(),
      outside_session_id: "o1",
      match: {
        patient: null,
        possible: [
          { patient_id: "p1", display_name: "Jane Doe", date_of_birth: null },
          { patient_id: "p2", display_name: "John Adams", date_of_birth: null },
        ],
        suggested_patient_id: "p1",
      },
    })

    render(<EditorialCalendar {...defaults()} defaultView="day" />, { wrapper: wrap() })
    fireEvent.click(screen.getByTestId("outside-session"))

    const dialog = screen.getByRole("dialog")
    expect(
      within(dialog).getByRole("combobox", { name: "Which client is J.A. Appointment?" })
    ).toHaveValue("p1")
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }))
    expect(answerMutateAsync).toHaveBeenCalledWith([
      {
        source: "ical:simplepractice",
        source_identifier: "J.A.",
        patient_id: "p1",
        new_client_name: null,
        not_a_client: false,
        outside_session_id: "o1",
      },
    ])
  })

  it("offers to make an inactive client active again, on by default", () => {
    QUESTIONS.push({ ...MATCHED, client_inactive: true })

    render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })
    fireEvent.click(
      within(screen.getByTestId("outside-sessions-line")).getByRole("button", { name: "Review" })
    )

    const dialog = screen.getByRole("dialog")
    const offer = within(dialog).getByRole("checkbox", { name: "Make Jane Doe active again" })
    expect(offer).toBeChecked()
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }))
    expect(answerMutateAsync).toHaveBeenCalledWith([
      {
        source: "google_calendar",
        source_identifier: "series:abc",
        patient_id: "p1",
        new_client_name: null,
        not_a_client: false,
        reactivate: true,
      },
    ])
  })

  it("books without reactivating when the offer is unticked, and drops it for another client", () => {
    QUESTIONS.push({ ...MATCHED, client_inactive: true })

    render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })
    fireEvent.click(
      within(screen.getByTestId("outside-sessions-line")).getByRole("button", { name: "Review" })
    )
    const dialog = screen.getByRole("dialog")
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Make Jane Doe active again" }))

    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }))
    expect(answerMutateAsync).toHaveBeenLastCalledWith([
      {
        source: "google_calendar",
        source_identifier: "series:abc",
        patient_id: "p1",
        new_client_name: null,
        not_a_client: false,
      },
    ])

    // Picking "New client" instead: nothing to reactivate.
    fireEvent.change(
      within(dialog).getByRole("combobox", { name: "Which client is Jane Doe?" }),
      { target: { value: "new" } }
    )
    expect(
      within(dialog).queryByRole("checkbox", { name: "Make Jane Doe active again" })
    ).not.toBeInTheDocument()
  })

  it("shows no such offer for an active client", () => {
    QUESTIONS.push(MATCHED)

    render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })
    fireEvent.click(
      within(screen.getByTestId("outside-sessions-line")).getByRole("button", { name: "Review" })
    )

    expect(
      within(screen.getByRole("dialog")).queryByRole("checkbox", { name: /active again/ })
    ).not.toBeInTheDocument()
  })

  it("answers one event from its block before the question list has loaded", async () => {
    const start = todayAt(14)
    OUTSIDE.push({
      id: "o1",
      source: "ical:simplepractice",
      source_identifier: "J.A.",
      title: "J.A. Appointment",
      start_at: start.toISOString(),
      end_at: new Date(start.getTime() + 50 * 60_000).toISOString(),
    })

    render(<EditorialCalendar {...defaults()} defaultView="day" />, { wrapper: wrap() })
    fireEvent.click(screen.getByTestId("outside-session"))
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }))

    expect(answerMutateAsync).toHaveBeenCalledWith([
      expect.objectContaining({ source_identifier: "J.A.", outside_session_id: "o1" }),
    ])
  })
})
