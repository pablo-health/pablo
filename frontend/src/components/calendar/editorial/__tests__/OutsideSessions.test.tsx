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
  answerMutateAsync.mockResolvedValue({ answered: 1, appointments_created: 1, appointments: [] })
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
})
