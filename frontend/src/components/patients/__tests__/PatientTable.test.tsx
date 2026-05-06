// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PatientTable Component Tests
 *
 * Tests table rendering, search/debounce, CRUD operations, and UI states.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { PatientTable } from "../PatientTable"
import * as patientsApi from "@/lib/api/patients"
import type { PatientResponse } from "@/types/patients"

// Mock Next.js router
const mockPush = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
  }),
}))

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

const mockPatients: PatientResponse[] = [
  {
    id: "patient-1",
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
    next_session_date: "2024-02-01T00:00:00Z",
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    deleted_at: null,
    restore_deadline: null,
  },
  {
    id: "patient-2",
    user_id: "user-1",
    first_name: "John",
    last_name: "Smith",
    email: null,
    phone: null,
    status: "inactive",
    date_of_birth: null,
    diagnosis: null,
    session_count: 0,
    last_session_date: null,
    next_session_date: null,
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    deleted_at: null,
    restore_deadline: null,
  },
  {
    id: "patient-3",
    user_id: "user-1",
    first_name: "Alice",
    last_name: "Johnson",
    email: "alice.johnson@example.com",
    phone: "(555) 987-6543",
    status: "on_hold",
    date_of_birth: "1990-07-22",
    diagnosis: "Depression",
    session_count: 12,
    last_session_date: "2024-01-15T00:00:00Z",
    next_session_date: null,
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    deleted_at: null,
    restore_deadline: null,
  },
]

describe("PatientTable", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPush.mockClear()
  })

  afterEach(() => {
    vi.clearAllTimers()
  })

  describe("Rendering", () => {
    it("renders patient table with data", async () => {
      const { Wrapper } = createWrapper()

      vi.mocked(patientsApi.listPatients).mockResolvedValue({
        data: mockPatients,
        total: 3,
        page: 1,
        page_size: 50,
      })

      render(<PatientTable />, { wrapper: Wrapper })

      // Wait for data to load
      await waitFor(() => {
        expect(screen.getByText("Jane Doe")).toBeInTheDocument()
      })

      expect(screen.getByText("John Smith")).toBeInTheDocument()
      expect(screen.getByText("Alice Johnson")).toBeInTheDocument()
    })

    it("displays patient email or N/A", async () => {
      const { Wrapper } = createWrapper()

      vi.mocked(patientsApi.listPatients).mockResolvedValue({
        data: mockPatients,
        total: 3,
        page: 1,
        page_size: 50,
      })

      render(<PatientTable />, { wrapper: Wrapper })

      await waitFor(() => {
        expect(screen.getByText("jane.doe@example.com")).toBeInTheDocument()
      })

      // John Smith has null email
      expect(screen.getAllByText("N/A").length).toBeGreaterThan(0)
    })

    it("displays patient phone or N/A", async () => {
      const { Wrapper } = createWrapper()

      vi.mocked(patientsApi.listPatients).mockResolvedValue({
        data: mockPatients,
        total: 3,
        page: 1,
        page_size: 50,
      })

      render(<PatientTable />, { wrapper: Wrapper })

      await waitFor(() => {
        expect(screen.getByText("(555) 123-4567")).toBeInTheDocument()
      })

      expect(screen.getByText("(555) 987-6543")).toBeInTheDocument()
    })

    it("displays status badges with correct styling", async () => {
      const { Wrapper } = createWrapper()

      vi.mocked(patientsApi.listPatients).mockResolvedValue({
        data: mockPatients,
        total: 3,
        page: 1,
        page_size: 50,
      })

      render(<PatientTable />, { wrapper: Wrapper })

      await waitFor(() => {
        expect(screen.getByText("active")).toBeInTheDocument()
      })

      expect(screen.getByText("inactive")).toBeInTheDocument()
      expect(screen.getByText("on hold")).toBeInTheDocument()
    })

    it("displays session count", async () => {
      const { Wrapper } = createWrapper()

      vi.mocked(patientsApi.listPatients).mockResolvedValue({
        data: mockPatients,
        total: 3,
        page: 1,
        page_size: 50,
      })

      render(<PatientTable />, { wrapper: Wrapper })

      await waitFor(() => {
        expect(screen.getByText("5")).toBeInTheDocument()
      })

      expect(screen.getByText("0")).toBeInTheDocument()
      expect(screen.getByText("12")).toBeInTheDocument()
    })

  })

  describe("Loading State", () => {
    it("shows loading message while fetching patients", () => {
      const { Wrapper } = createWrapper()

      vi.mocked(patientsApi.listPatients).mockImplementation(
        () => new Promise(() => {}) // Never resolves
      )

      render(<PatientTable />, { wrapper: Wrapper })

      expect(screen.getByText(/loading patients/i)).toBeInTheDocument()
    })
  })

  describe("Error State", () => {
    it("shows error message when API fails", async () => {
      const { Wrapper } = createWrapper()

      vi.mocked(patientsApi.listPatients).mockRejectedValue(new Error("API Error"))

      render(<PatientTable />, { wrapper: Wrapper })

      await waitFor(() => {
        expect(screen.getByText(/failed to load patients/i)).toBeInTheDocument()
      })
    })
  })

  describe("Empty State", () => {
    it("shows empty state when no patients exist", async () => {
      const { Wrapper } = createWrapper()

      vi.mocked(patientsApi.listPatients).mockResolvedValue({
        data: [],
        total: 0,
        page: 1,
        page_size: 50,
      })

      render(<PatientTable />, { wrapper: Wrapper })

      await waitFor(() => {
        expect(
          screen.getByText(/no patients yet.*click.*add patient.*to get started/i)
        ).toBeInTheDocument()
      })
    })

    it("shows no results message when search returns empty", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()

      // Initial load with data
      vi.mocked(patientsApi.listPatients).mockResolvedValue({
        data: mockPatients,
        total: 3,
        page: 1,
        page_size: 50,
      })

      render(<PatientTable />, { wrapper: Wrapper })

      await waitFor(() => {
        expect(screen.getByText("Jane Doe")).toBeInTheDocument()
      })

      // Search returns no results
      vi.mocked(patientsApi.listPatients).mockResolvedValue({
        data: [],
        total: 0,
        page: 1,
        page_size: 50,
      })

      const searchInput = screen.getByPlaceholderText(/search patients/i)
      await user.type(searchInput, "NonexistentName")

      // Wait for debounce (500ms) + API call
      await waitFor(
        () => {
          expect(
            screen.getByText(/no patients found matching your search/i)
          ).toBeInTheDocument()
        },
        { timeout: 1000 }
      )
    })
  })

  describe("Search Functionality", () => {
    it("renders search input", async () => {
      const { Wrapper } = createWrapper()

      vi.mocked(patientsApi.listPatients).mockResolvedValue({
        data: mockPatients,
        total: 3,
        page: 1,
        page_size: 50,
      })

      render(<PatientTable />, { wrapper: Wrapper })

      expect(screen.getByPlaceholderText(/search patients by name/i)).toBeInTheDocument()
    })
  })

  describe("Delete confirmation modal", () => {
    const openDeleteModal = async (user: ReturnType<typeof userEvent.setup>) => {
      const { Wrapper } = createWrapper()
      vi.mocked(patientsApi.listPatients).mockResolvedValue({
        data: mockPatients,
        total: 3,
        page: 1,
        page_size: 50,
      })
      render(<PatientTable />, { wrapper: Wrapper })
      await waitFor(() => {
        expect(screen.getByText("Jane Doe")).toBeInTheDocument()
      })
      const deleteBtn = screen.getByRole("button", {
        name: /delete patient jane doe/i,
      })
      await user.click(deleteBtn)
      const dialog = await screen.findByRole("dialog")
      return dialog
    }

    it("disables Delete until the retention attestation checkbox is checked", async () => {
      const user = userEvent.setup()
      const dialog = await openDeleteModal(user)

      const confirmBtn = within(dialog).getByRole("button", { name: /^delete$/i })
      expect(confirmBtn).toBeDisabled()

      const checkbox = within(dialog).getByRole("checkbox", {
        name: /met my professional retention obligations/i,
      })
      await user.click(checkbox)

      expect(confirmBtn).toBeEnabled()
    })

    it("sends acknowledged_retention_obligation: true with the delete call", async () => {
      vi.mocked(patientsApi.deletePatient).mockResolvedValue({
        message: "Patient and 5 sessions deleted successfully",
      })

      const user = userEvent.setup()
      const dialog = await openDeleteModal(user)

      await user.click(
        within(dialog).getByRole("checkbox", {
          name: /met my professional retention obligations/i,
        }),
      )
      await user.click(within(dialog).getByRole("button", { name: /^delete$/i }))

      await waitFor(() => {
        expect(patientsApi.deletePatient).toHaveBeenCalledWith(
          "patient-1",
          true,
          undefined,
        )
      })
    })

    it("resets the checkbox when the dialog is reopened", async () => {
      const user = userEvent.setup()
      const { Wrapper } = createWrapper()
      vi.mocked(patientsApi.listPatients).mockResolvedValue({
        data: mockPatients,
        total: 3,
        page: 1,
        page_size: 50,
      })
      render(<PatientTable />, { wrapper: Wrapper })

      await waitFor(() => {
        expect(screen.getByText("Jane Doe")).toBeInTheDocument()
      })

      // Open, check the box, then cancel — the box should not persist.
      await user.click(
        screen.getByRole("button", { name: /delete patient jane doe/i }),
      )
      const firstDialog = await screen.findByRole("dialog")
      await user.click(
        within(firstDialog).getByRole("checkbox", {
          name: /met my professional retention obligations/i,
        }),
      )
      expect(
        within(firstDialog).getByRole("button", { name: /^delete$/i }),
      ).toBeEnabled()
      await user.click(
        within(firstDialog).getByRole("button", { name: /cancel/i }),
      )

      await waitFor(() => {
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
      })

      // Reopen for a different patient — Delete must start disabled again.
      await user.click(
        screen.getByRole("button", { name: /delete patient john smith/i }),
      )
      const secondDialog = await screen.findByRole("dialog")
      expect(
        within(secondDialog).getByRole("checkbox", {
          name: /met my professional retention obligations/i,
        }),
      ).not.toBeChecked()
      expect(
        within(secondDialog).getByRole("button", { name: /^delete$/i }),
      ).toBeDisabled()
    })
  })
})
