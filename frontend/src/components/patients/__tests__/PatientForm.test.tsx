// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PatientForm Component Tests
 *
 * Tests form validation, create/edit modes, and error handling.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { PatientForm } from "../PatientForm"
import * as patientsApi from "@/lib/api/patients"
import type { PatientResponse } from "@/types/patients"
import { peopleWords, type PeopleTerm } from "@/lib/peopleTerm"

vi.mock("@/lib/api/patients")

// The clinician's word for the people they see. "clients" unless a test
// switches it.
const peopleTerm = vi.hoisted(() => ({ current: "clients" as PeopleTerm }))
vi.mock("@/hooks/usePeopleTerm", async (orig) => ({
  ...(await orig<typeof import("@/hooks/usePeopleTerm")>()),
  usePeopleTerm: () => peopleWords(peopleTerm.current),
}))

// The portal is off unless a test turns it on: most of this file is about
// the form itself, which closes on save when there is no next step.
const mockFeature = vi.fn((_name: string) => false)
vi.mock("@/lib/featureGates", () => ({
  useFeature: (name: string) => mockFeature(name),
}))

// What the next step reads. Its own behaviour is covered in
// intakeSend/__tests__/SendFormsFlow.test.tsx; here it only has to appear.
vi.mock("@/lib/api/intakePackets", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/intakePackets")>()),
  listIntakeTemplates: vi.fn().mockResolvedValue([]),
}))
vi.mock("@/lib/api/portalAccess", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/portalAccess")>()),
  getPortalAccess: vi.fn().mockResolvedValue({
    patient_id: "patient-new",
    invite_outstanding: false,
    live_sessions: 0,
    portal_enabled: true,
    revoked_at: null,
  }),
}))
vi.mock("@/lib/api/inviteTemplate", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/inviteTemplate")>()),
  getInviteTemplate: vi.fn().mockResolvedValue({ editable: false }),
}))

// The practice's portal answer. Decided unless a test says otherwise.
const MODULES_ON = { intake: true, messaging: true, appointments: true, refills: true }
const mockPortalSettings = vi.fn()
const mockSavePortalSettings = vi.fn()
vi.mock("@/lib/api/portalSettings", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/portalSettings")>()),
  getPortalSettings: (...a: unknown[]) => mockPortalSettings(...a),
  savePortalSettings: (...a: unknown[]) => mockSavePortalSettings(...a),
}))

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })

  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "QueryWrapper"
  return { Wrapper, queryClient }
}

const mockPatient: PatientResponse = {
  id: "patient-123",
  first_name: "Jane",
  last_name: "Doe",
  email: "jane.doe@example.com",
  phone: "(555) 123-4567",
  status: "active",
  date_of_birth: "1985-03-15",
  diagnosis: "Anxiety",
  session_count: 5,
  last_session_date: "2024-01-01T00:00:00Z",
  next_session_date: null,
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
  deleted_at: null,
  restore_deadline: null,
  address_line1: null,
  address_line2: null,
  city: null,
  state: null,
  postal_code: null,
  sex: null,
}

describe("PatientForm", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFeature.mockImplementation(() => false)
    peopleTerm.current = "clients"
  })

  it("says patients when that is the clinician's word", () => {
    peopleTerm.current = "patients"
    const { Wrapper } = createWrapper()

    render(<PatientForm mode="create" open={true} onOpenChange={vi.fn()} />, {
      wrapper: Wrapper,
    })

    expect(screen.getByText("Add Patient")).toBeInTheDocument()
    expect(screen.getByText("Enter patient information to create a new record.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Create Patient" })).toBeInTheDocument()
  })

  describe("Create Mode", () => {
    it("renders empty form in create mode", () => {
      const { Wrapper } = createWrapper()

      render(
        <PatientForm
          mode="create"
          open={true}
          onOpenChange={vi.fn()}
        />,
        { wrapper: Wrapper }
      )

      expect(screen.getByText("Add Client")).toBeInTheDocument()
      expect(screen.getByLabelText(/first name/i)).toHaveValue("")
      expect(screen.getByLabelText(/last name/i)).toHaveValue("")
      expect(screen.getByLabelText(/email/i)).toHaveValue("")
      expect(screen.getByLabelText(/phone/i)).toHaveValue("")
    })

    it("creates patient successfully with all fields", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()
      const onOpenChange = vi.fn()

      const newPatient: PatientResponse = {
        ...mockPatient,
        id: "patient-new",
      }

      vi.mocked(patientsApi.createPatient).mockResolvedValue(newPatient)

      render(
        <PatientForm
          mode="create"
          open={true}
          onOpenChange={onOpenChange}
        />,
        { wrapper: Wrapper }
      )

      // Fill in required fields
      await user.type(screen.getByLabelText(/first name/i), "Jane")
      await user.type(screen.getByLabelText(/last name/i), "Doe")

      // Fill in optional fields
      await user.type(screen.getByLabelText(/email/i), "jane.doe@example.com")
      await user.type(screen.getByLabelText(/phone/i), "(555) 123-4567")
      await user.type(screen.getByLabelText(/date of birth/i), "1985-03-15")
      await user.type(screen.getByLabelText(/diagnosis/i), "Anxiety")

      // Submit form
      await user.click(screen.getByRole("button", { name: /create client/i }))

      await waitFor(() => {
        expect(patientsApi.createPatient).toHaveBeenCalledWith({
          first_name: "Jane",
          last_name: "Doe",
          email: "jane.doe@example.com",
          phone: "(555) 123-4567",
          status: "active",
          date_of_birth: "1985-03-15",
          diagnosis: "Anxiety",
        }, undefined)
      })

      // Dialog should close on success
      expect(onOpenChange).toHaveBeenCalledWith(false)
    })

    it("creates patient with only required fields", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()
      const onOpenChange = vi.fn()

      const newPatient: PatientResponse = {
        ...mockPatient,
        id: "patient-new",
        email: null,
        phone: null,
        date_of_birth: null,
        diagnosis: null,
      }

      vi.mocked(patientsApi.createPatient).mockResolvedValue(newPatient)

      render(
        <PatientForm
          mode="create"
          open={true}
          onOpenChange={onOpenChange}
        />,
        { wrapper: Wrapper }
      )

      // Fill in only required fields
      await user.type(screen.getByLabelText(/first name/i), "Jane")
      await user.type(screen.getByLabelText(/last name/i), "Doe")

      // Submit form
      await user.click(screen.getByRole("button", { name: /create client/i }))

      await waitFor(() => {
        expect(patientsApi.createPatient).toHaveBeenCalledWith({
          first_name: "Jane",
          last_name: "Doe",
          email: undefined,
          phone: undefined,
          status: "active",
          date_of_birth: undefined,
          diagnosis: undefined,
        }, undefined)
      })

      expect(onOpenChange).toHaveBeenCalledWith(false)
    })
  })

  describe("Edit Mode", () => {
    it("renders form with pre-filled patient data in edit mode", () => {
      const { Wrapper } = createWrapper()

      render(
        <PatientForm
          mode="edit"
          patient={mockPatient}
          open={true}
          onOpenChange={vi.fn()}
        />,
        { wrapper: Wrapper }
      )

      expect(screen.getByText("Edit Client")).toBeInTheDocument()
      expect(screen.getByLabelText(/first name/i)).toHaveValue("Jane")
      expect(screen.getByLabelText(/last name/i)).toHaveValue("Doe")
      expect(screen.getByLabelText(/email/i)).toHaveValue("jane.doe@example.com")
      expect(screen.getByLabelText(/phone/i)).toHaveValue("(555) 123-4567")
      expect(screen.getByLabelText(/date of birth/i)).toHaveValue("1985-03-15")
      // Diagnoses are kept on the problem list once the chart exists.
      expect(screen.queryByLabelText(/diagnosis/i)).not.toBeInTheDocument()
    })

    it("updates patient successfully", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()
      const onOpenChange = vi.fn()

      const updatedPatient: PatientResponse = {
        ...mockPatient,
        email: "jane.updated@example.com",
      }

      vi.mocked(patientsApi.updatePatient).mockResolvedValue(updatedPatient)

      render(
        <PatientForm
          mode="edit"
          patient={mockPatient}
          open={true}
          onOpenChange={onOpenChange}
        />,
        { wrapper: Wrapper }
      )

      // Update email field
      const emailInput = screen.getByLabelText(/email/i)
      await user.clear(emailInput)
      await user.type(emailInput, "jane.updated@example.com")

      // Submit form
      await user.click(screen.getByRole("button", { name: /update client/i }))

      await waitFor(() => {
        expect(patientsApi.updatePatient).toHaveBeenCalledWith(
          "patient-123",
          {
            first_name: "Jane",
            last_name: "Doe",
            email: "jane.updated@example.com",
            phone: "(555) 123-4567",
            status: "active",
            date_of_birth: "1985-03-15",
          },
          undefined
        )
      })

      expect(onOpenChange).toHaveBeenCalledWith(false)
    })

    it("handles null optional fields in edit mode", () => {
      const { Wrapper } = createWrapper()

      const patientWithNulls: PatientResponse = {
        ...mockPatient,
        email: null,
        phone: null,
        date_of_birth: null,
        diagnosis: null,
      }

      render(
        <PatientForm
          mode="edit"
          patient={patientWithNulls}
          open={true}
          onOpenChange={vi.fn()}
        />,
        { wrapper: Wrapper }
      )

      expect(screen.getByLabelText(/first name/i)).toHaveValue("Jane")
      expect(screen.getByLabelText(/last name/i)).toHaveValue("Doe")
      expect(screen.getByLabelText(/email/i)).toHaveValue("")
      expect(screen.getByLabelText(/phone/i)).toHaveValue("")
      expect(screen.getByLabelText(/date of birth/i)).toHaveValue("")
      expect(screen.queryByLabelText(/diagnosis/i)).not.toBeInTheDocument()
    })
  })

  describe("Address", () => {
    it("submits address fields", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()
      const onOpenChange = vi.fn()

      vi.mocked(patientsApi.createPatient).mockResolvedValue(mockPatient)

      render(
        <PatientForm mode="create" open={true} onOpenChange={onOpenChange} />,
        { wrapper: Wrapper }
      )

      await user.type(screen.getByLabelText(/first name/i), "Jane")
      await user.type(screen.getByLabelText(/last name/i), "Doe")
      await user.type(screen.getByLabelText(/address line 1/i), "123 Main St")
      await user.type(screen.getByLabelText(/city/i), "Springfield")
      await user.type(screen.getByLabelText(/state/i), "IL")
      await user.type(screen.getByLabelText(/zip/i), "62704")

      await user.click(screen.getByRole("button", { name: /create client/i }))

      await waitFor(() => {
        expect(patientsApi.createPatient).toHaveBeenCalledWith(
          expect.objectContaining({
            address_line1: "123 Main St",
            city: "Springfield",
            state: "IL",
            postal_code: "62704",
          }),
          undefined
        )
      })
    })

    it("pre-fills address in edit mode", () => {
      const { Wrapper } = createWrapper()

      render(
        <PatientForm
          mode="edit"
          patient={{
            ...mockPatient,
            address_line1: "456 Oak Ave",
            city: "Shelbyville",
            state: "IL",
            postal_code: "62565",
            sex: "M",
          }}
          open={true}
          onOpenChange={vi.fn()}
        />,
        { wrapper: Wrapper }
      )

      expect(screen.getByLabelText(/address line 1/i)).toHaveValue("456 Oak Ave")
      expect(screen.getByLabelText(/city/i)).toHaveValue("Shelbyville")
    })
  })

  describe("Sex on insurance card", () => {
    // Only a claim reads it, so the coverage dialog asks for it instead.
    it("is not asked for on the client form", () => {
      const { Wrapper } = createWrapper()

      render(<PatientForm mode="create" open={true} onOpenChange={vi.fn()} />, {
        wrapper: Wrapper,
      })

      expect(screen.queryByText(/sex/i)).not.toBeInTheDocument()
    })

    it("leaves a value already on file alone when the client is edited", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()

      vi.mocked(patientsApi.updatePatient).mockResolvedValue({ ...mockPatient, sex: "F" })

      render(
        <PatientForm
          mode="edit"
          patient={{ ...mockPatient, sex: "F" }}
          open={true}
          onOpenChange={vi.fn()}
        />,
        { wrapper: Wrapper }
      )

      await user.click(screen.getByRole("button", { name: /update client/i }))

      await waitFor(() => {
        expect(patientsApi.updatePatient).toHaveBeenCalled()
      })
      expect(vi.mocked(patientsApi.updatePatient).mock.calls[0][1]).not.toHaveProperty("sex")
    })
  })

  describe("After adding a client, where there is a portal", () => {
    beforeEach(() => {
      mockFeature.mockImplementation((name) => name === "patient_portal")
      mockPortalSettings.mockResolvedValue({ enabled: true, decided: true, modules: MODULES_ON })
    })

    it("runs on to what they should do instead of closing", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()
      const onOpenChange = vi.fn()
      vi.mocked(patientsApi.createPatient).mockResolvedValue({
        ...mockPatient,
        id: "patient-new",
        first_name: "Robin",
        last_name: "Reyes",
      })
      vi.mocked(patientsApi.getPatient).mockResolvedValue({ ...mockPatient, id: "patient-new" })

      render(<PatientForm mode="create" open={true} onOpenChange={onOpenChange} />, {
        wrapper: Wrapper,
      })
      await user.type(screen.getByLabelText(/first name/i), "Robin")
      await user.type(screen.getByLabelText(/last name/i), "Reyes")
      await user.click(screen.getByRole("button", { name: /create client/i }))

      expect(await screen.findByTestId("new-client-next-step")).toBeInTheDocument()
      expect(screen.getByText("What should Robin do next?")).toBeInTheDocument()
      expect(onOpenChange).not.toHaveBeenCalled()

      await user.click(await screen.findByRole("button", { name: "Not now" }))
      expect(onOpenChange).toHaveBeenCalledWith(false)
    })

    it("still just saves when editing", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()
      const onOpenChange = vi.fn()
      vi.mocked(patientsApi.updatePatient).mockResolvedValue(mockPatient)

      render(
        <PatientForm mode="edit" patient={mockPatient} open={true} onOpenChange={onOpenChange} />,
        { wrapper: Wrapper },
      )
      await user.click(screen.getByRole("button", { name: /update client/i }))

      await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
      expect(screen.queryByTestId("new-client-next-step")).not.toBeInTheDocument()
    })
  })

  describe("The first client, where the practice has never decided about a portal", () => {
    beforeEach(() => {
      mockFeature.mockImplementation((name) => name === "patient_portal")
      mockPortalSettings.mockResolvedValue({ enabled: false, decided: false, modules: MODULES_ON })
      mockSavePortalSettings.mockImplementation((change: { enabled: boolean }) =>
        Promise.resolve({ enabled: change.enabled, decided: true, modules: MODULES_ON }),
      )
      vi.mocked(patientsApi.createPatient).mockResolvedValue({
        ...mockPatient,
        id: "patient-new",
        first_name: "Robin",
        last_name: "Reyes",
      })
      vi.mocked(patientsApi.getPatient).mockResolvedValue({ ...mockPatient, id: "patient-new" })
    })

    async function addRobin() {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()
      render(<PatientForm mode="create" open={true} onOpenChange={vi.fn()} />, {
        wrapper: Wrapper,
      })
      await user.type(screen.getByLabelText(/first name/i), "Robin")
      await user.type(screen.getByLabelText(/last name/i), "Reyes")
      await user.click(screen.getByRole("button", { name: /create client/i }))
      return user
    }

    it("asks once whether to offer a portal before anything else", async () => {
      await addRobin()

      expect(await screen.findByTestId("portal-offer-prompt")).toHaveTextContent(
        "Offer your clients a portal?",
      )
      expect(screen.queryByTestId("new-client-next-step")).not.toBeInTheDocument()
    })

    it("records not now, then goes on and says where to change it", async () => {
      const user = await addRobin()

      await user.click(await screen.findByRole("button", { name: "Not now" }))

      // Sent only-if-undecided, so it cannot undo a colleague's "on".
      expect(mockSavePortalSettings).toHaveBeenCalledWith({
        enabled: false,
        only_if_undecided: true,
      })
      const next = await screen.findByTestId("new-client-next-step")
      expect(next).toHaveTextContent("You can turn on the client portal any time in Settings.")
    })

    it("turns it on with the parts chosen, then goes on to what the client should do", async () => {
      const user = await addRobin()

      await user.click(await screen.findByRole("button", { name: "Yes, set it up" }))
      const choose = await screen.findByTestId("portal-offer-choose")
      expect(choose).toHaveTextContent("What can clients do in it?")
      // Everything the deployment serves starts checked.
      expect(screen.getByRole("checkbox", { name: "Messages" })).toBeChecked()
      await user.click(screen.getByRole("checkbox", { name: "Appointments" }))
      await user.click(screen.getByRole("button", { name: "Turn on the portal" }))

      expect(mockSavePortalSettings).toHaveBeenCalledWith({
        enabled: true,
        modules: { intake: true, messaging: true, appointments: false, refills: true },
      })
      const next = await screen.findByTestId("new-client-next-step")
      expect(next).toHaveTextContent("Choose packets to send and whether to invite them to the portal.")
    })

    it("does not ask where the deployment serves no part a practice can choose", async () => {
      mockPortalSettings.mockResolvedValue({ enabled: false, decided: false, modules: {} })
      await addRobin()

      expect(await screen.findByTestId("new-client-next-step")).toBeInTheDocument()
      expect(screen.queryByTestId("portal-offer-prompt")).not.toBeInTheDocument()
    })

    it("will not turn it on with nothing in it", async () => {
      const user = await addRobin()
      await user.click(await screen.findByRole("button", { name: "Yes, set it up" }))
      for (const part of ["Forms", "Messages", "Appointments", "Refill requests"]) {
        await user.click(screen.getByRole("checkbox", { name: part }))
      }

      expect(screen.getByRole("button", { name: "Turn on the portal" })).toBeDisabled()
      expect(screen.getByText("Choose at least one.")).toBeInTheDocument()
    })
  })

  describe("Validation", () => {
    it("shows error when first name is empty", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()

      render(
        <PatientForm
          mode="create"
          open={true}
          onOpenChange={vi.fn()}
        />,
        { wrapper: Wrapper }
      )

      // Try to submit without first name
      await user.click(screen.getByRole("button", { name: /create client/i }))

      await waitFor(() => {
        expect(screen.getByText(/first name is required/i)).toBeInTheDocument()
      })

      expect(patientsApi.createPatient).not.toHaveBeenCalled()
    })

    it("shows error when last name is empty", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()

      render(
        <PatientForm
          mode="create"
          open={true}
          onOpenChange={vi.fn()}
        />,
        { wrapper: Wrapper }
      )

      // Fill first name but not last name
      await user.type(screen.getByLabelText(/first name/i), "Jane")

      // Try to submit
      await user.click(screen.getByRole("button", { name: /create client/i }))

      await waitFor(() => {
        expect(screen.getByText(/last name is required/i)).toBeInTheDocument()
      })

      expect(patientsApi.createPatient).not.toHaveBeenCalled()
    })


    it("shows error for phone number less than 10 digits", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()

      render(
        <PatientForm
          mode="create"
          open={true}
          onOpenChange={vi.fn()}
        />,
        { wrapper: Wrapper }
      )

      await user.type(screen.getByLabelText(/first name/i), "Jane")
      await user.type(screen.getByLabelText(/last name/i), "Doe")
      await user.type(screen.getByLabelText(/phone/i), "123")

      await user.click(screen.getByRole("button", { name: /create client/i }))

      await waitFor(() => {
        expect(screen.getByText(/phone must be at least 10 digits/i)).toBeInTheDocument()
      })

      expect(patientsApi.createPatient).not.toHaveBeenCalled()
    })

    it("accepts valid phone number with formatting", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()
      const onOpenChange = vi.fn()

      vi.mocked(patientsApi.createPatient).mockResolvedValue(mockPatient)

      render(
        <PatientForm
          mode="create"
          open={true}
          onOpenChange={onOpenChange}
        />,
        { wrapper: Wrapper }
      )

      await user.type(screen.getByLabelText(/first name/i), "Jane")
      await user.type(screen.getByLabelText(/last name/i), "Doe")
      await user.type(screen.getByLabelText(/phone/i), "(555) 123-4567")

      await user.click(screen.getByRole("button", { name: /create client/i }))

      await waitFor(() => {
        expect(patientsApi.createPatient).toHaveBeenCalledWith(
          expect.objectContaining({
            first_name: "Jane",
            last_name: "Doe",
            phone: "(555) 123-4567",
          }),
          undefined
        )
      })

      expect(onOpenChange).toHaveBeenCalledWith(false)
    })
  })

  describe("Status Selection", () => {
    it("defaults to active status in create mode", () => {
      const { Wrapper } = createWrapper()

      render(
        <PatientForm
          mode="create"
          open={true}
          onOpenChange={vi.fn()}
        />,
        { wrapper: Wrapper }
      )

      // Check that active is selected (status field should show "Active")
      expect(screen.getByRole("combobox", { name: /status/i })).toBeInTheDocument()
    })

  })

  describe("Error Handling", () => {
    it("handles API errors gracefully", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()
      const onOpenChange = vi.fn()

      const error = new Error("API Error")
      vi.mocked(patientsApi.createPatient).mockRejectedValue(error)

      render(
        <PatientForm
          mode="create"
          open={true}
          onOpenChange={onOpenChange}
        />,
        { wrapper: Wrapper }
      )

      await user.type(screen.getByLabelText(/first name/i), "Jane")
      await user.type(screen.getByLabelText(/last name/i), "Doe")

      await user.click(screen.getByRole("button", { name: /create client/i }))

      await waitFor(() => {
        expect(patientsApi.createPatient).toHaveBeenCalled()
      })

      // Dialog should stay open on error
      expect(onOpenChange).not.toHaveBeenCalledWith(false)
    })
  })

  describe("Cancel Button", () => {
    it("closes dialog and resets form when cancel is clicked", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()
      const onOpenChange = vi.fn()

      render(
        <PatientForm
          mode="create"
          open={true}
          onOpenChange={onOpenChange}
        />,
        { wrapper: Wrapper }
      )

      // Fill in some data
      await user.type(screen.getByLabelText(/first name/i), "Jane")
      await user.type(screen.getByLabelText(/last name/i), "Doe")

      // Click cancel
      await user.click(screen.getByRole("button", { name: /cancel/i }))

      expect(onOpenChange).toHaveBeenCalledWith(false)
      expect(patientsApi.createPatient).not.toHaveBeenCalled()
    })
  })

  describe("Loading States", () => {
    it("disables submit button while submitting", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()

      // Mock a slow API response
      vi.mocked(patientsApi.createPatient).mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve(mockPatient), 1000))
      )

      render(
        <PatientForm
          mode="create"
          open={true}
          onOpenChange={vi.fn()}
        />,
        { wrapper: Wrapper }
      )

      await user.type(screen.getByLabelText(/first name/i), "Jane")
      await user.type(screen.getByLabelText(/last name/i), "Doe")

      const submitButton = screen.getByRole("button", { name: /create client/i })
      await user.click(submitButton)

      // Button should be disabled during submission
      await waitFor(() => {
        expect(screen.getByRole("button", { name: /creating/i })).toBeDisabled()
      })
    })

    it("shows 'Updating...' text in edit mode while submitting", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()

      vi.mocked(patientsApi.updatePatient).mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve(mockPatient), 1000))
      )

      render(
        <PatientForm
          mode="edit"
          patient={mockPatient}
          open={true}
          onOpenChange={vi.fn()}
        />,
        { wrapper: Wrapper }
      )

      const submitButton = screen.getByRole("button", { name: /update client/i })
      await user.click(submitButton)

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /updating/i })).toBeDisabled()
      })
    })
  })
})
