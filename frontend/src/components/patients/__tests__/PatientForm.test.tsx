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

vi.mock("@/lib/api/patients")

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
  user_id: "user-1",
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
}

describe("PatientForm", () => {
  beforeEach(() => {
    vi.clearAllMocks()
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

      expect(screen.getByText("Add Patient")).toBeInTheDocument()
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
      await user.click(screen.getByRole("button", { name: /create patient/i }))

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
      await user.click(screen.getByRole("button", { name: /create patient/i }))

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

      expect(screen.getByText("Edit Patient")).toBeInTheDocument()
      expect(screen.getByLabelText(/first name/i)).toHaveValue("Jane")
      expect(screen.getByLabelText(/last name/i)).toHaveValue("Doe")
      expect(screen.getByLabelText(/email/i)).toHaveValue("jane.doe@example.com")
      expect(screen.getByLabelText(/phone/i)).toHaveValue("(555) 123-4567")
      expect(screen.getByLabelText(/date of birth/i)).toHaveValue("1985-03-15")
      expect(screen.getByLabelText(/diagnosis/i)).toHaveValue("Anxiety")
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
      await user.click(screen.getByRole("button", { name: /update patient/i }))

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
            diagnosis: "Anxiety",
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
      expect(screen.getByLabelText(/diagnosis/i)).toHaveValue("")
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
      await user.click(screen.getByRole("button", { name: /create patient/i }))

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
      await user.click(screen.getByRole("button", { name: /create patient/i }))

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

      await user.click(screen.getByRole("button", { name: /create patient/i }))

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

      await user.click(screen.getByRole("button", { name: /create patient/i }))

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

      await user.click(screen.getByRole("button", { name: /create patient/i }))

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

      const submitButton = screen.getByRole("button", { name: /create patient/i })
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

      const submitButton = screen.getByRole("button", { name: /update patient/i })
      await user.click(submitButton)

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /updating/i })).toBeDisabled()
      })
    })
  })
})
