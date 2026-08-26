// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, it, expect, vi } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { AppointmentModal } from "../AppointmentModal"
import type { UserPreferences } from "@/lib/api/users"
import type { AppointmentResponse } from "@/types/scheduling"
import type { PatientListParams } from "@/types/patients"

const {
  mockCreate,
  mockCreateRecurring,
  mockUpdate,
  mockCancel,
  mockEditSeries,
  mockCancelSeries,
  mockUsePatientList,
} = vi.hoisted(() => ({
  mockCreate: vi.fn(),
  mockCreateRecurring: vi.fn(),
  mockUpdate: vi.fn(),
  mockCancel: vi.fn(),
  mockEditSeries: vi.fn(),
  mockCancelSeries: vi.fn(),
  mockUsePatientList: vi.fn(),
}))

// p3 only turns up once a search is in flight, standing in for a patient
// past the roster's default (unfiltered) first page.
const ALL_PATIENTS = [
  { id: "p1", first_name: "Jane", last_name: "Doe" },
  { id: "p2", first_name: "John", last_name: "Smith" },
  { id: "p3", first_name: "Priya", last_name: "Nguyen" },
]

vi.mock("@/hooks/usePatients", () => ({
  usePatientList: (params?: PatientListParams) => mockUsePatientList(params),
}))

vi.mock("@/hooks/useAppointments", () => ({
  useCreateAppointment: () => ({ mutate: mockCreate, isPending: false }),
  useCreateRecurringAppointment: () => ({ mutate: mockCreateRecurring, isPending: false }),
  useUpdateAppointment: () => ({ mutate: mockUpdate, isPending: false }),
  useCancelAppointment: () => ({ mutate: mockCancel, isPending: false }),
  useEditAppointmentSeries: () => ({ mutate: mockEditSeries, isPending: false }),
  useCancelAppointmentSeries: () => ({ mutate: mockCancelSeries, isPending: false }),
}))

vi.mock("@/hooks/useNoteTypes", () => ({
  useNoteTypes: () => ({
    data: {
      note_types: [
        {
          key: "soap",
          label: "SOAP",
          description: "Subjective / Objective / Assessment / Plan",
          tier: "core",
          context: "session",
          sections: [],
        },
        {
          key: "narrative",
          label: "Narrative",
          description: "Free-form narrative note",
          tier: "core",
          context: "session",
          sections: [],
        },
      ],
    },
  }),
}))

vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))

const baseAppointment: AppointmentResponse = {
  id: "a1",
  user_id: "u1",
  patient_id: "p1",
  title: "Old Title",
  start_at: "2026-03-20T10:00:00Z",
  end_at: "2026-03-20T10:50:00Z",
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
  created_at: "2026-03-20T09:00:00Z",
  updated_at: null,
}

const recurringAppointment: AppointmentResponse = {
  ...baseAppointment,
  id: "a2",
  recurring_appointment_id: "series-1",
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "TestQueryClientWrapper"
  return Wrapper
}

describe("AppointmentModal", () => {
  beforeEach(() => {
    mockUsePatientList.mockImplementation((params?: PatientListParams) => {
      const search = params?.search?.toLowerCase()
      if (search) {
        const data = ALL_PATIENTS.filter((p) =>
          `${p.first_name} ${p.last_name}`.toLowerCase().includes(search),
        )
        return { data: { data, total: data.length, page: 1, page_size: data.length } }
      }
      const data = ALL_PATIENTS.slice(0, 2)
      return { data: { data, total: ALL_PATIENTS.length, page: 1, page_size: 2 } }
    })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it("renders the new-appointment header when no appointment provided", () => {
    render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.getByText("New appointment")).toBeInTheDocument()
  })

  it("renders the edit-appointment header when an appointment is provided", () => {
    render(<AppointmentModal open onClose={vi.fn()} appointment={baseAppointment} />, {
      wrapper: createWrapper(),
    })
    expect(screen.getByText("Edit appointment")).toBeInTheDocument()
  })

  it("renders the fast-path field labels", () => {
    render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.getByText("Patient")).toBeInTheDocument()
    expect(screen.getByText("When")).toBeInTheDocument()
    expect(screen.getByText("Length")).toBeInTheDocument()
    expect(screen.getByText("Session type")).toBeInTheDocument()
  })

  it("does not render a primary Title field (title is a tucked-away caption)", () => {
    render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })
    // No patient chosen yet => no title caption either.
    expect(screen.queryByText(/^Title:/)).not.toBeInTheDocument()
  })

  it("disables the Schedule button until a patient is chosen", () => {
    render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.getByRole("button", { name: "Schedule" })).toBeDisabled()
  })

  it("offers quick-pick length chips with 45 selected by default", () => {
    render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })
    for (const min of [30, 45, 50, 60, 90]) {
      expect(screen.getByRole("button", { name: `${min} min` })).toBeInTheDocument()
    }
    expect(screen.getByRole("button", { name: "45 min" })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
  })

  it("uses the preference default duration as the selected chip", () => {
    const prefs = { default_duration_minutes: 60 } as UserPreferences
    render(<AppointmentModal open onClose={vi.fn()} preferences={prefs} />, {
      wrapper: createWrapper(),
    })
    expect(screen.getByRole("button", { name: "60 min" })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
  })

  it("lets the user pick a different length chip", async () => {
    const user = userEvent.setup()
    render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })
    await user.click(screen.getByRole("button", { name: "30 min" }))
    expect(screen.getByRole("button", { name: "30 min" })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
  })

  it("adds a custom length chip and selects it", async () => {
    const user = userEvent.setup()
    render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })
    await user.click(screen.getByRole("button", { name: /add/i }))
    const input = screen.getByLabelText("Custom length")
    await user.type(input, "25{Enter}")
    expect(screen.getByRole("button", { name: "25 min" })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
  })

  it("renders a Session type segmented control with Individual checked", () => {
    render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })
    const group = screen.getByRole("radiogroup", { name: /session type/i })
    expect(within(group).getByRole("radio", { name: "Individual" })).toHaveAttribute(
      "aria-checked",
      "true",
    )
  })

  it("tucks video link, note type, and notes behind More options", async () => {
    const user = userEvent.setup()
    render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })
    expect(screen.queryByLabelText("Video link")).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /more options/i }))
    expect(screen.getByLabelText("Video link")).toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: /note type/i })).toBeInTheDocument()
    expect(screen.getByLabelText("Notes")).toBeInTheDocument()
  })

  it("auto-expands More options in edit mode when notes exist", () => {
    render(
      <AppointmentModal
        open
        onClose={vi.fn()}
        appointment={{ ...baseAppointment, notes: "prior note" }}
      />,
      { wrapper: createWrapper() },
    )
    expect(screen.getByLabelText("Notes")).toBeInTheDocument()
  })

  it("adds a non-standard edit duration to the chip list, pre-selected", () => {
    render(
      <AppointmentModal
        open
        onClose={vi.fn()}
        appointment={{ ...baseAppointment, duration_minutes: 75 }}
      />,
      { wrapper: createWrapper() },
    )
    expect(screen.getByRole("button", { name: "75 min" })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
  })

  it("shows the destructive Cancel appointment action only in edit mode", () => {
    const { rerender } = render(<AppointmentModal open onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })
    expect(
      screen.queryByRole("button", { name: /cancel appointment/i }),
    ).not.toBeInTheDocument()
    rerender(<AppointmentModal open onClose={vi.fn()} appointment={baseAppointment} />)
    expect(
      screen.getByRole("button", { name: /cancel appointment/i }),
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Save changes" })).toBeInTheDocument()
  })

  it("calls onClose when the secondary footer button is clicked", async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(<AppointmentModal open onClose={onClose} />, { wrapper: createWrapper() })
    await user.click(screen.getByRole("button", { name: "Cancel" }))
    expect(onClose).toHaveBeenCalled()
  })

  it("does not render when closed", () => {
    render(<AppointmentModal open={false} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })
    expect(screen.queryByText("New appointment")).not.toBeInTheDocument()
  })

  describe("Auto-title caption", () => {
    it("auto-generates the title caption when a patient is selected", async () => {
      const user = userEvent.setup()
      render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })

      const patientTrigger = screen.getByRole("combobox", { name: /patient/i })
      await user.click(patientTrigger)
      await user.click(screen.getByRole("option", { name: /Doe, Jane/i }))

      expect(screen.getByText("Jane Doe — Individual")).toBeInTheDocument()
    })

    it("lets the user override the title and reset it back to auto", async () => {
      const user = userEvent.setup()
      render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })

      const patientTrigger = screen.getByRole("combobox", { name: /patient/i })
      await user.click(patientTrigger)
      await user.click(screen.getByRole("option", { name: /Doe, Jane/i }))

      await user.click(screen.getByRole("button", { name: "Edit" }))
      const titleInput = screen.getByLabelText("Title") as HTMLInputElement
      await user.clear(titleInput)
      await user.type(titleInput, "Custom Title")
      expect(titleInput.value).toBe("Custom Title")

      // Blur out of the inline editor to commit the override.
      await user.tab()
      expect(screen.getByText("Custom Title")).toBeInTheDocument()

      await user.click(screen.getByRole("button", { name: "reset" }))
      expect(screen.getByText("Jane Doe — Individual")).toBeInTheDocument()
    })

    it("finds and books a patient that only turns up via search, outside the first page", async () => {
      const user = userEvent.setup()
      render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })

      const patientTrigger = screen.getByRole("combobox", { name: /patient/i })
      await user.click(patientTrigger)
      expect(screen.queryByRole("option", { name: /Nguyen, Priya/i })).not.toBeInTheDocument()

      await user.type(patientTrigger, "priya")
      await waitFor(() => {
        expect(screen.getByRole("option", { name: /Nguyen, Priya/i })).toBeInTheDocument()
      })
      await user.click(screen.getByRole("option", { name: /Nguyen, Priya/i }))

      expect(screen.getByText("Priya Nguyen — Individual")).toBeInTheDocument()

      await user.click(screen.getByRole("button", { name: "Schedule" }))
      expect(mockCreate).toHaveBeenCalledTimes(1)
      expect(mockCreate.mock.calls[0][0]).toMatchObject({ patient_id: "p3" })
    })
  })

  describe("Truncated roster hint", () => {
    it("shows a count hint when the roster exceeds the loaded page and the query is empty", async () => {
      const user = userEvent.setup()
      render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })

      const patientTrigger = screen.getByRole("combobox", { name: /patient/i })
      await user.click(patientTrigger)

      expect(screen.getByText("Showing first 2 of 3 — type to search")).toBeInTheDocument()
      const options = screen.getAllByRole("option")
      expect(options).toHaveLength(2)
    })

    it("hides the hint once the user starts searching", async () => {
      const user = userEvent.setup()
      render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })

      const patientTrigger = screen.getByRole("combobox", { name: /patient/i })
      await user.click(patientTrigger)
      await user.type(patientTrigger, "priya")

      await waitFor(() => {
        expect(screen.getByRole("option", { name: /Nguyen, Priya/i })).toBeInTheDocument()
      })
      expect(screen.queryByText(/Showing first/i)).not.toBeInTheDocument()
    })

    it("does not show the hint when the roster fits within the loaded page", async () => {
      mockUsePatientList.mockReturnValue({
        data: { data: ALL_PATIENTS, total: ALL_PATIENTS.length, page: 1, page_size: 100 },
      })
      const user = userEvent.setup()
      render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })

      const patientTrigger = screen.getByRole("combobox", { name: /patient/i })
      await user.click(patientTrigger)

      expect(screen.queryByText(/Showing first/i)).not.toBeInTheDocument()
    })
  })

  describe("Note type picker", () => {
    it("defaults to SOAP and lets the user pick another", async () => {
      const user = userEvent.setup()
      render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })
      await user.click(screen.getByRole("button", { name: /more options/i }))
      const trigger = screen.getByRole("combobox", { name: /note type/i })
      expect(trigger).toHaveTextContent("SOAP")
      await user.click(trigger)
      await user.click(screen.getByRole("option", { name: /narrative/i }))
      expect(trigger).toHaveTextContent("Narrative")
    })

    it("submits the selected note type when creating an appointment", async () => {
      const user = userEvent.setup()
      render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })
      const patientTrigger = screen.getByRole("combobox", { name: /patient/i })
      await user.click(patientTrigger)
      await user.click(screen.getByRole("option", { name: /Doe, Jane/i }))
      await user.click(screen.getByRole("button", { name: /more options/i }))
      await user.click(screen.getByRole("combobox", { name: /note type/i }))
      await user.click(screen.getByRole("option", { name: /narrative/i }))
      await user.click(screen.getByRole("button", { name: "Schedule" }))

      expect(mockCreate).toHaveBeenCalledTimes(1)
      expect(mockCreate.mock.calls[0][0]).toMatchObject({ note_type: "narrative" })
    })

    it("pre-selects the note type stored on the appointment when editing", async () => {
      render(
        <AppointmentModal
          open
          onClose={vi.fn()}
          appointment={{ ...baseAppointment, note_type: "narrative" }}
        />,
        { wrapper: createWrapper() },
      )
      const user = userEvent.setup()
      await user.click(screen.getByRole("button", { name: /more options/i }))
      expect(screen.getByRole("combobox", { name: /note type/i })).toHaveTextContent("Narrative")

      await user.click(screen.getByRole("button", { name: "Save changes" }))
      expect(mockUpdate).toHaveBeenCalledTimes(1)
      expect(mockUpdate.mock.calls[0][0].data).toMatchObject({ note_type: "narrative" })
    })
  })

  describe("Video platform default", () => {
    it("carries the user's default video platform onto a newly created appointment", async () => {
      const user = userEvent.setup()
      const prefs = { default_video_platform: "google_meet" } as UserPreferences
      render(<AppointmentModal open onClose={vi.fn()} preferences={prefs} />, {
        wrapper: createWrapper(),
      })
      const patientTrigger = screen.getByRole("combobox", { name: /patient/i })
      await user.click(patientTrigger)
      await user.click(screen.getByRole("option", { name: /Doe, Jane/i }))
      await user.click(screen.getByRole("button", { name: "Schedule" }))

      const [payload] = mockCreate.mock.calls[0]
      expect(payload).toMatchObject({ video_platform: "google_meet" })
    })

    it("keeps an existing appointment's video platform on edit", async () => {
      const user = userEvent.setup()
      const appointment = { ...baseAppointment, video_platform: "zoom" }
      const prefs = { default_video_platform: "google_meet" } as UserPreferences
      render(
        <AppointmentModal open onClose={vi.fn()} appointment={appointment} preferences={prefs} />,
        { wrapper: createWrapper() },
      )
      await user.click(screen.getByRole("button", { name: "Save changes" }))

      expect(mockUpdate.mock.calls[0][0].data).toMatchObject({ video_platform: "zoom" })
    })
  })

  describe("Recurrence (create mode only)", () => {
    async function selectPatient(user: ReturnType<typeof userEvent.setup>) {
      const patientTrigger = screen.getByRole("combobox", { name: /patient/i })
      await user.click(patientTrigger)
      await user.click(screen.getByRole("option", { name: /Doe, Jane/i }))
    }

    it("does not render a Repeats control in edit mode", () => {
      render(<AppointmentModal open onClose={vi.fn()} appointment={baseAppointment} />, {
        wrapper: createWrapper(),
      })
      expect(screen.queryByRole("radiogroup", { name: /repeats/i })).not.toBeInTheDocument()
    })

    it("submits the legacy single-create payload when Repeats is left at None", async () => {
      const user = userEvent.setup()
      render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })
      await selectPatient(user)
      await user.click(screen.getByRole("button", { name: "Schedule" }))

      expect(mockCreate).toHaveBeenCalledTimes(1)
      expect(mockCreateRecurring).not.toHaveBeenCalled()
      const [payload] = mockCreate.mock.calls[0]
      expect(payload).toMatchObject({ patient_id: "p1", session_type: "individual" })
      expect(payload).not.toHaveProperty("frequency")
    })

    it("submits the recurring payload with an IANA timezone for a weekly repeat with a session count", async () => {
      const user = userEvent.setup()
      render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })
      await selectPatient(user)

      await user.click(screen.getByRole("radio", { name: "Weekly" }))
      await user.type(screen.getByLabelText("Number of sessions"), "6")
      await user.click(screen.getByRole("button", { name: "Schedule" }))

      expect(mockCreateRecurring).toHaveBeenCalledTimes(1)
      expect(mockCreate).not.toHaveBeenCalled()
      const [payload] = mockCreateRecurring.mock.calls[0]
      expect(payload).toMatchObject({
        patient_id: "p1",
        frequency: "weekly",
        count: 6,
        end_date: null,
      })
      expect(typeof payload.timezone).toBe("string")
      expect(payload.timezone.length).toBeGreaterThan(0)
    })

    it("does not offer a Monthly option", () => {
      render(<AppointmentModal open onClose={vi.fn()} />, { wrapper: createWrapper() })
      expect(screen.queryByRole("radio", { name: /monthly/i })).not.toBeInTheDocument()
    })
  })

  describe("Recurring series scope (edit mode only)", () => {
    it("does not render a scope chooser for a non-recurring appointment", async () => {
      const user = userEvent.setup()
      render(<AppointmentModal open onClose={vi.fn()} appointment={baseAppointment} />, {
        wrapper: createWrapper(),
      })

      expect(screen.queryByRole("radiogroup", { name: /applies to/i })).not.toBeInTheDocument()

      await user.click(screen.getByRole("button", { name: "Save changes" }))
      expect(mockUpdate).toHaveBeenCalledTimes(1)
      expect(mockEditSeries).not.toHaveBeenCalled()

      await user.click(screen.getByRole("button", { name: /cancel appointment/i }))
      expect(mockCancel).toHaveBeenCalledTimes(1)
      expect(mockCancelSeries).not.toHaveBeenCalled()
    })

    it("saves this occurrence only via the existing PATCH when 'Just this session' is kept", async () => {
      const user = userEvent.setup()
      render(<AppointmentModal open onClose={vi.fn()} appointment={recurringAppointment} />, {
        wrapper: createWrapper(),
      })

      const group = screen.getByRole("radiogroup", { name: /applies to/i })
      expect(within(group).getByRole("radio", { name: /just this session/i })).toHaveAttribute(
        "aria-checked",
        "true",
      )

      await user.click(screen.getByRole("button", { name: "Save changes" }))

      expect(mockUpdate).toHaveBeenCalledTimes(1)
      expect(mockUpdate.mock.calls[0][0]).toMatchObject({ appointmentId: "a2" })
      expect(mockEditSeries).not.toHaveBeenCalled()
    })

    it("saves the whole series via edit-series when 'This and future sessions' is chosen", async () => {
      const user = userEvent.setup()
      render(<AppointmentModal open onClose={vi.fn()} appointment={recurringAppointment} />, {
        wrapper: createWrapper(),
      })

      const group = screen.getByRole("radiogroup", { name: /applies to/i })
      await user.click(within(group).getByRole("radio", { name: /this and future sessions/i }))
      await user.click(screen.getByRole("button", { name: "Save changes" }))

      expect(mockEditSeries).toHaveBeenCalledTimes(1)
      expect(mockUpdate).not.toHaveBeenCalled()
      const [args] = mockEditSeries.mock.calls[0]
      expect(args).toMatchObject({ appointmentId: "a2" })
    })

    it("cancels the whole series via cancel-series when 'This and future sessions' is chosen", async () => {
      const user = userEvent.setup()
      render(<AppointmentModal open onClose={vi.fn()} appointment={recurringAppointment} />, {
        wrapper: createWrapper(),
      })

      const group = screen.getByRole("radiogroup", { name: /applies to/i })
      await user.click(within(group).getByRole("radio", { name: /this and future sessions/i }))
      await user.click(screen.getByRole("button", { name: /cancel appointment/i }))

      expect(mockCancelSeries).toHaveBeenCalledTimes(1)
      expect(mockCancelSeries).toHaveBeenCalledWith("a2", expect.anything())
      expect(mockCancel).not.toHaveBeenCalled()
    })
  })
})
