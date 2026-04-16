// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { AppointmentModal } from "../AppointmentModal"

vi.mock("@/hooks/usePatients", () => ({
  usePatientList: () => ({
    data: {
      data: [
        { id: "p1", first_name: "Jane", last_name: "Doe" },
        { id: "p2", first_name: "John", last_name: "Smith" },
      ],
    },
  }),
}))

vi.mock("@/hooks/useAppointments", () => ({
  useCreateAppointment: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateAppointment: () => ({ mutate: vi.fn(), isPending: false }),
  useCancelAppointment: () => ({ mutate: vi.fn(), isPending: false }),
}))

vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))

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
  it("renders new appointment title when no appointment provided", () => {
    render(
      <AppointmentModal open onClose={vi.fn()} />,
      { wrapper: createWrapper() }
    )
    expect(screen.getByText("New Appointment")).toBeInTheDocument()
  })

  it("renders edit appointment title when appointment provided", () => {
    const appointment = {
      id: "a1",
      user_id: "u1",
      patient_id: "p1",
      title: "Session",
      start_at: "2026-03-20T10:00:00Z",
      end_at: "2026-03-20T10:50:00Z",
      duration_minutes: 50,
      status: "confirmed" as const,
      session_type: "individual",
      video_link: null,
      video_platform: null,
      notes: null,
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

    render(
      <AppointmentModal open onClose={vi.fn()} appointment={appointment} />,
      { wrapper: createWrapper() }
    )
    expect(screen.getByText("Edit Appointment")).toBeInTheDocument()
  })

  it("has a dialog description for accessibility", () => {
    render(
      <AppointmentModal open onClose={vi.fn()} />,
      { wrapper: createWrapper() }
    )
    expect(
      screen.getByText("Fill in the details to schedule a new appointment.")
    ).toBeInTheDocument()
  })

  it("renders form section labels", () => {
    render(
      <AppointmentModal open onClose={vi.fn()} />,
      { wrapper: createWrapper() }
    )
    expect(screen.getByText("Patient & Title")).toBeInTheDocument()
    expect(screen.getByText("Schedule")).toBeInTheDocument()
    expect(screen.getByText("Session Details")).toBeInTheDocument()
    // "Notes" appears as both section label and form label
    expect(screen.getAllByText("Notes")).toHaveLength(2)
  })

  it("disables Create button when patient is not selected", () => {
    render(
      <AppointmentModal open onClose={vi.fn()} />,
      { wrapper: createWrapper() }
    )
    const createButton = screen.getByRole("button", { name: "Create" })
    expect(createButton).toBeDisabled()
  })

  it("calls onClose when Close button is clicked", async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()

    render(
      <AppointmentModal open onClose={onClose} />,
      { wrapper: createWrapper() }
    )

    // Multiple "close" buttons (radix X + our button) — find the explicit one
    const closeButtons = screen.getAllByRole("button", { name: /close/i })
    const explicitClose = closeButtons.find((btn) => btn.textContent === "Close")!
    await user.click(explicitClose)
    expect(onClose).toHaveBeenCalled()
  })

  it("does not render when closed", () => {
    render(
      <AppointmentModal open={false} onClose={vi.fn()} />,
      { wrapper: createWrapper() }
    )
    expect(screen.queryByText("New Appointment")).not.toBeInTheDocument()
  })

  describe("Preferences as Defaults", () => {
    const prefs = {
      default_video_platform: "teams",
      default_session_type: "couples",
      default_duration_minutes: 60,
      auto_transcribe: true,
      quality_preset: "balanced",
      therapist_display_name: null,
      working_hours_start: 8,
      working_hours_end: 18,
      calendar_default_view: "timeGridWeek",
      timezone: "America/New_York",
    }

    it("uses preference duration for new appointments", () => {
      render(
        <AppointmentModal open onClose={vi.fn()} preferences={prefs} />,
        { wrapper: createWrapper() }
      )
      const durationInput = screen.getByLabelText("Duration (min)") as HTMLInputElement
      expect(durationInput.value).toBe("60")
    })

    it("uses appointment values over preferences when editing", () => {
      const appointment = {
        id: "a1",
        user_id: "u1",
        patient_id: "p1",
        title: "Session",
        start_at: "2026-03-20T10:00:00Z",
        end_at: "2026-03-20T10:50:00Z",
        duration_minutes: 50,
        status: "confirmed" as const,
        session_type: "individual",
        video_link: null,
        video_platform: null,
        notes: null,
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

      render(
        <AppointmentModal
          open
          onClose={vi.fn()}
          appointment={appointment}
          preferences={prefs}
        />,
        { wrapper: createWrapper() }
      )
      const durationInput = screen.getByLabelText("Duration (min)") as HTMLInputElement
      expect(durationInput.value).toBe("50")
    })
  })

  describe("Auto-title Generation", () => {
    it("auto-generates title when patient is selected", async () => {
      const user = userEvent.setup()
      render(
        <AppointmentModal open onClose={vi.fn()} />,
        { wrapper: createWrapper() }
      )

      // Open patient dropdown and select Jane Doe
      const patientTrigger = screen.getByRole("combobox", { name: /patient/i })
      await user.click(patientTrigger)
      const option = screen.getByRole("option", { name: /Doe, Jane/i })
      await user.click(option)

      const titleInput = screen.getByLabelText("Title") as HTMLInputElement
      expect(titleInput.value).toBe("Jane Doe - Individual")
    })

    it("preserves manual title edits", async () => {
      const user = userEvent.setup()
      render(
        <AppointmentModal open onClose={vi.fn()} />,
        { wrapper: createWrapper() }
      )

      // Manually type a title first
      const titleInput = screen.getByLabelText("Title") as HTMLInputElement
      await user.type(titleInput, "Custom Title")

      // Select a patient — should NOT overwrite manual title
      const patientTrigger = screen.getByRole("combobox", { name: /patient/i })
      await user.click(patientTrigger)
      const option = screen.getByRole("option", { name: /Doe, Jane/i })
      await user.click(option)

      expect(titleInput.value).toBe("Custom Title")
    })
  })
})
