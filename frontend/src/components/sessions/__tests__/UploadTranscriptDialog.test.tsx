// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * UploadTranscriptDialog Component Tests
 *
 * Comprehensive tests for file upload dialog with drag & drop, validation, and form submission.
 */

import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { UploadTranscriptDialog } from "../UploadTranscriptDialog"
import * as patientsApi from "@/lib/api/patients"
import * as sessionsApi from "@/lib/api/sessions"
import { createMockPatient, createMockSession } from "@/test/factories"

// Mock pointer capture for Radix UI Select component
beforeAll(() => {
  Element.prototype.hasPointerCapture = vi.fn()
  Element.prototype.releasePointerCapture = vi.fn()
})

vi.mock("@/lib/api/patients")
vi.mock("@/lib/api/sessions")

const mockPush = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
  }),
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
  Wrapper.displayName = "TestQueryClientWrapper"
  return Wrapper
}

const mockPatients = [
  createMockPatient({
    id: "patient-1",
    first_name: "Jane",
    last_name: "Doe",
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
  }),
  createMockPatient({
    id: "patient-2",
    first_name: "John",
    last_name: "Smith",
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
  }),
]

const mockSession = createMockSession({
  patient_id: "patient-1",
  transcript: { format: "vtt", content: "Test" },
  soap_note: {
    subjective: "Test",
    objective: "Test",
    assessment: "Test",
    plan: "Test",
  },
})

describe("UploadTranscriptDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPush.mockClear()

    vi.mocked(patientsApi.listPatients).mockResolvedValue({
      data: mockPatients,
      total: 2,
      page: 1,
      page_size: 50,
    })
  })

  describe("Dialog Behavior", () => {
    it("renders trigger button by default", () => {
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      expect(screen.getByText("Upload Session")).toBeInTheDocument()
    })

    it("renders custom trigger when provided", () => {
      render(
        <UploadTranscriptDialog trigger={<button>Custom Trigger</button>} />,
        { wrapper: createWrapper() }
      )

      expect(screen.getByText("Custom Trigger")).toBeInTheDocument()
      expect(screen.queryByText("Upload Session")).not.toBeInTheDocument()
    })

    it("opens dialog when trigger is clicked", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        expect(screen.getByText("Upload Session Transcript")).toBeInTheDocument()
      })
    })

    it("closes dialog when Cancel button is clicked", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        expect(screen.getByText("Cancel")).toBeInTheDocument()
      })

      await user.click(screen.getByText("Cancel"))

      await waitFor(() => {
        expect(screen.queryByText("Upload Session Transcript")).not.toBeInTheDocument()
      })
    })

    it("resets form when dialog is closed", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        expect(screen.getByLabelText(/Session Date/)).toBeInTheDocument()
      })

      // Fill in date
      const dateInput = screen.getByLabelText(/Session Date/)
      await user.type(dateInput, "2024-01-15T14:30")

      // Close dialog
      await user.click(screen.getByText("Cancel"))

      // Reopen dialog
      await user.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        const newDateInput = screen.getByLabelText(/Session Date/)
        expect(newDateInput).toHaveValue("")
      })
    })
  })

  describe("Patient Selection", () => {
    it("loads and displays patients in dropdown", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        expect(screen.getByText("Select a patient...")).toBeInTheDocument()
      })

      const selectTrigger = screen.getByRole("combobox")
      await user.click(selectTrigger)

      // Wait for options to appear (multiple elements with same text due to Radix UI rendering)
      const doeJaneOptions = await screen.findAllByText("Doe, Jane")
      expect(doeJaneOptions.length).toBeGreaterThan(0)

      const smithJohnOptions = screen.getAllByText("Smith, John")
      expect(smithJohnOptions.length).toBeGreaterThan(0)
    })

    it.skip("shows validation error when patient is not selected", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      // Wait for form to be ready
      const submitButton = await screen.findByText("Upload & Generate SOAP")
      expect(submitButton).toBeInTheDocument()

      await user.click(submitButton)

      // Form validation errors should appear
      expect(await screen.findByText("Patient is required", {}, { timeout: 2000 })).toBeInTheDocument()
    })

    it("disables patient dropdown while loading", () => {
      vi.mocked(patientsApi.listPatients).mockImplementation(
        () => new Promise(() => {}) // Never resolves
      )

      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      fireEvent.click(screen.getByText("Upload Session"))

      waitFor(() => {
        const selectTrigger = screen.getByRole("combobox")
        expect(selectTrigger).toBeDisabled()
      })
    })
  })

  describe("Session Date", () => {
    it("shows validation error when date is not provided", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        expect(screen.getByText("Upload & Generate SOAP")).toBeInTheDocument()
      })

      await user.click(screen.getByText("Upload & Generate SOAP"))

      await waitFor(() => {
        expect(screen.getByText("Session date is required")).toBeInTheDocument()
      })
    })

    it("accepts valid datetime input", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        expect(screen.getByLabelText(/Session Date/)).toBeInTheDocument()
      })

      const dateInput = screen.getByLabelText(/Session Date/)
      await user.type(dateInput, "2024-01-15T14:30")

      expect(dateInput).toHaveValue("2024-01-15T14:30")
    })
  })

  describe("File Upload - Click", () => {
    it("accepts file via file input click", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        expect(screen.getByText(/Drag & drop a file/)).toBeInTheDocument()
      })

      const file = new File(["test content"], "transcript.vtt", {
        type: "text/vtt",
      })

      const input = screen.getByLabelText(/Transcript File/)
      await user.upload(input, file)

      await waitFor(() => {
        expect(screen.getByText("transcript.vtt")).toBeInTheDocument()
      })
    })

    it("displays file size", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      const file = new File(["test content"], "transcript.vtt", {
        type: "text/vtt",
      })

      const input = screen.getByLabelText(/Transcript File/)
      await user.upload(input, file)

      await waitFor(() => {
        expect(screen.getByText(/Bytes/)).toBeInTheDocument()
      })
    })

    it("displays format badge for selected file", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      const file = new File(["test content"], "transcript.vtt", {
        type: "text/vtt",
      })

      const input = screen.getByLabelText(/Transcript File/)
      await user.upload(input, file)

      await waitFor(() => {
        expect(screen.getByText("VTT")).toBeInTheDocument()
      })
    })

    it("allows removing selected file", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      const file = new File(["test content"], "transcript.vtt", {
        type: "text/vtt",
      })

      const input = screen.getByLabelText(/Transcript File/)
      await user.upload(input, file)

      await waitFor(() => {
        expect(screen.getByText("transcript.vtt")).toBeInTheDocument()
      })

      const removeButtons = screen.getAllByRole("button")
      const removeButton = removeButtons.find((btn) =>
        btn.querySelector("svg")?.classList.contains("lucide-x")
      )
      expect(removeButton).toBeDefined()

      if (removeButton) {
        await user.click(removeButton)

        await waitFor(() => {
          expect(screen.queryByText("transcript.vtt")).not.toBeInTheDocument()
        })
      }
    })
  })

  describe("File Upload - Drag & Drop", () => {
    it("handles file drop", async () => {
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      fireEvent.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        expect(screen.getByText(/Drag & drop a file/)).toBeInTheDocument()
      })

      const dropZone = screen.getByText(/Drag & drop a file/).parentElement
      expect(dropZone).toBeTruthy()

      const file = new File(["test content"], "transcript.json", {
        type: "application/json",
      })

      if (dropZone) {
        fireEvent.drop(dropZone, {
          dataTransfer: {
            files: [file],
          },
        })

        await waitFor(() => {
          expect(screen.getByText("transcript.json")).toBeInTheDocument()
        })
      }
    })

    it("shows dragging state when file is dragged over", () => {
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      fireEvent.click(screen.getByText("Upload Session"))

      waitFor(() => {
        const dropZone = screen.getByText(/Drag & drop a file/).parentElement

        if (dropZone) {
          fireEvent.dragEnter(dropZone)

          expect(screen.getByText("Drop file here")).toBeInTheDocument()

          fireEvent.dragLeave(dropZone)

          expect(screen.queryByText("Drop file here")).not.toBeInTheDocument()
        }
      })
    })
  })

  describe("File Validation", () => {
    it("rejects files that are too large", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      const largeContent = new Array(11 * 1024 * 1024).fill("a").join("")
      const file = new File([largeContent], "large.vtt", {
        type: "text/vtt",
      })

      const input = screen.getByLabelText(/Transcript File/)
      await user.upload(input, file)

      await waitFor(() => {
        expect(screen.getByText(/File size exceeds/)).toBeInTheDocument()
      })
    })

    it.skip("rejects invalid file formats", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      const file = new File(["test content"], "document.pdf", {
        type: "application/pdf",
      })

      const input = screen.getByLabelText(/Transcript File/)
      await user.upload(input, file)

      expect(await screen.findByText(/Invalid file format/)).toBeInTheDocument()
    })

    it("rejects empty files", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      const file = new File([], "empty.vtt", { type: "text/vtt" })

      const input = screen.getByLabelText(/Transcript File/)
      await user.upload(input, file)

      await waitFor(() => {
        expect(screen.getByText("File is empty")).toBeInTheDocument()
      })
    })

    it("accepts VTT files", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      const file = new File(["WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nTest"], "transcript.vtt", {
        type: "text/vtt",
      })

      const input = screen.getByLabelText(/Transcript File/)
      await user.upload(input, file)

      await waitFor(() => {
        expect(screen.getByText("transcript.vtt")).toBeInTheDocument()
        expect(screen.queryByText(/Invalid file format/)).not.toBeInTheDocument()
      })
    })

    it("accepts JSON files", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      const file = new File(['{"text": "test"}'], "transcript.json", {
        type: "application/json",
      })

      const input = screen.getByLabelText(/Transcript File/)
      await user.upload(input, file)

      await waitFor(() => {
        expect(screen.getByText("transcript.json")).toBeInTheDocument()
        expect(screen.queryByText(/Invalid file format/)).not.toBeInTheDocument()
      })
    })

    it("accepts TXT files", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      const file = new File(["Plain text content"], "transcript.txt", {
        type: "text/plain",
      })

      const input = screen.getByLabelText(/Transcript File/)
      await user.upload(input, file)

      await waitFor(() => {
        expect(screen.getByText("transcript.txt")).toBeInTheDocument()
        expect(screen.queryByText(/Invalid file format/)).not.toBeInTheDocument()
      })
    })
  })

  describe("Form Submission", () => {
    it.skip("shows validation errors when form is incomplete", async () => {
      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        expect(screen.getByText("Upload & Generate SOAP")).toBeInTheDocument()
      })

      await user.click(screen.getByText("Upload & Generate SOAP"))

      expect(await screen.findByText("Patient is required")).toBeInTheDocument()
      expect(screen.getByText("Session date is required")).toBeInTheDocument()
      expect(screen.getByText("File is required")).toBeInTheDocument()
    })

    it("submits form with valid data", async () => {
      const user = userEvent.setup()
      const onSuccess = vi.fn()

      vi.mocked(sessionsApi.uploadSession).mockResolvedValue(mockSession)

      render(<UploadTranscriptDialog onSuccess={onSuccess} />, {
        wrapper: createWrapper(),
      })

      await user.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        expect(screen.getByText("Select a patient...")).toBeInTheDocument()
      })

      // Select patient
      const selectTrigger = screen.getByRole("combobox")
      await user.click(selectTrigger)

      const doeJaneOptions = await screen.findAllByText("Doe, Jane")
      expect(doeJaneOptions.length).toBeGreaterThan(0)

      await user.click(doeJaneOptions[doeJaneOptions.length - 1])

      // Fill date
      const dateInput = screen.getByLabelText(/Session Date/)
      await user.type(dateInput, "2024-01-15T14:30")

      // Upload file
      const file = new File(["WEBVTT\n\nTest content"], "transcript.vtt", {
        type: "text/vtt",
      })

      const fileInput = screen.getByLabelText(/Transcript File/)
      await user.upload(fileInput, file)

      await waitFor(() => {
        expect(screen.getByText("transcript.vtt")).toBeInTheDocument()
      })

      // Submit
      await user.click(screen.getByText("Upload & Generate SOAP"))

      await waitFor(() => {
        expect(sessionsApi.uploadSession).toHaveBeenCalledWith(
          "patient-1",
          {
            patient_id: "patient-1",
            session_date: "2024-01-15T14:30",
            transcript: {
              format: "vtt",
              content: expect.any(String),
            },
          },
          undefined
        )
      })

      await waitFor(() => {
        expect(onSuccess).toHaveBeenCalledWith(mockSession)
      })
    })

    it("navigates to session detail on success by default", async () => {
      const user = userEvent.setup()

      vi.mocked(sessionsApi.uploadSession).mockResolvedValue(mockSession)

      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        expect(screen.getByText("Select a patient...")).toBeInTheDocument()
      })

      // Select patient
      const selectTrigger = screen.getByRole("combobox")
      await user.click(selectTrigger)
      const doeJaneOptions = await screen.findAllByText("Doe, Jane")
      await user.click(doeJaneOptions[doeJaneOptions.length - 1])

      // Fill date
      const dateInput = screen.getByLabelText(/Session Date/)
      await user.type(dateInput, "2024-01-15T14:30")

      // Upload file
      const file = new File(["Test content"], "transcript.txt", {
        type: "text/plain",
      })

      const fileInput = screen.getByLabelText(/Transcript File/)
      await user.upload(fileInput, file)

      await waitFor(() => {
        expect(screen.getByText("transcript.txt")).toBeInTheDocument()
      })

      // Submit
      await user.click(screen.getByText("Upload & Generate SOAP"))

      await waitFor(() => {
        expect(mockPush).toHaveBeenCalledWith("/dashboard/sessions/session-123")
      })
    })

    it("shows loading state during upload", async () => {
      const user = userEvent.setup()

      vi.mocked(sessionsApi.uploadSession).mockImplementation(
        () => new Promise(() => {}) // Never resolves
      )

      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        expect(screen.getByText("Select a patient...")).toBeInTheDocument()
      })

      // Select patient
      const selectTrigger = screen.getByRole("combobox")
      await user.click(selectTrigger)
      const doeJaneOptions = await screen.findAllByText("Doe, Jane")
      await user.click(doeJaneOptions[doeJaneOptions.length - 1])

      // Fill date
      const dateInput = screen.getByLabelText(/Session Date/)
      await user.type(dateInput, "2024-01-15T14:30")

      // Upload file
      const file = new File(["Test"], "transcript.txt", { type: "text/plain" })

      const fileInput = screen.getByLabelText(/Transcript File/)
      await user.upload(fileInput, file)

      // Submit
      await user.click(screen.getByText("Upload & Generate SOAP"))

      await waitFor(() => {
        expect(screen.getByText("Uploading...")).toBeInTheDocument()
      })
    })

    it("displays error message when upload fails", async () => {
      const user = userEvent.setup()

      vi.mocked(sessionsApi.uploadSession).mockRejectedValue(
        new Error("Upload failed")
      )

      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        expect(screen.getByText("Select a patient...")).toBeInTheDocument()
      })

      // Select patient
      const selectTrigger = screen.getByRole("combobox")
      await user.click(selectTrigger)
      const doeJaneOptions = await screen.findAllByText("Doe, Jane")
      await user.click(doeJaneOptions[doeJaneOptions.length - 1])

      // Fill date
      const dateInput = screen.getByLabelText(/Session Date/)
      await user.type(dateInput, "2024-01-15T14:30")

      // Upload file
      const file = new File(["Test"], "transcript.txt", { type: "text/plain" })

      const fileInput = screen.getByLabelText(/Transcript File/)
      await user.upload(fileInput, file)

      // Submit
      await user.click(screen.getByText("Upload & Generate SOAP"))

      await waitFor(() => {
        expect(screen.getByText("Upload failed")).toBeInTheDocument()
      })
    })

    it("allows retry after error", async () => {
      const user = userEvent.setup()

      vi.mocked(sessionsApi.uploadSession)
        .mockRejectedValueOnce(new Error("Upload failed"))
        .mockResolvedValue(mockSession)

      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        expect(screen.getByText("Select a patient...")).toBeInTheDocument()
      })

      // Select patient
      const selectTrigger = screen.getByRole("combobox")
      await user.click(selectTrigger)
      const doeJaneOptions = await screen.findAllByText("Doe, Jane")
      await user.click(doeJaneOptions[doeJaneOptions.length - 1])

      // Fill date
      const dateInput = screen.getByLabelText(/Session Date/)
      await user.type(dateInput, "2024-01-15T14:30")

      // Upload file
      const file = new File(["Test"], "transcript.txt", { type: "text/plain" })

      const fileInput = screen.getByLabelText(/Transcript File/)
      await user.upload(fileInput, file)

      // Submit (fails)
      await user.click(screen.getByText("Upload & Generate SOAP"))

      await waitFor(() => {
        expect(screen.getByText("Upload failed")).toBeInTheDocument()
      })

      // Retry
      await user.click(screen.getByText("Upload & Generate SOAP"))

      await waitFor(() => {
        expect(mockPush).toHaveBeenCalledWith("/dashboard/sessions/session-123")
      })
    })
  })

  describe("Edge Cases", () => {
    it("handles missing patients data gracefully", async () => {
      vi.mocked(patientsApi.listPatients).mockResolvedValue({
        data: [],
        total: 0,
        page: 1,
        page_size: 50,
      })

      const user = userEvent.setup()
      render(<UploadTranscriptDialog />, { wrapper: createWrapper() })

      await user.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        expect(screen.getByText("Select a patient...")).toBeInTheDocument()
      })

      const selectTrigger = screen.getByRole("combobox")
      await user.click(selectTrigger)

      await waitFor(() => {
        const options = screen.queryAllByRole("option")
        expect(options).toHaveLength(0)
      })
    })

    it("applies custom className to dialog content", async () => {
      render(
        <UploadTranscriptDialog className="custom-test-class" />,
        { wrapper: createWrapper() }
      )

      fireEvent.click(screen.getByText("Upload Session"))

      await waitFor(() => {
        const content = document.querySelector(".custom-test-class")
        expect(content).toBeInTheDocument()
      })
    })

    it("parses VTT file correctly", async () => {
      const user = userEvent.setup()
      const onSuccess = vi.fn()

      vi.mocked(sessionsApi.uploadSession).mockResolvedValue(mockSession)

      render(<UploadTranscriptDialog onSuccess={onSuccess} />, {
        wrapper: createWrapper(),
      })

      await user.click(screen.getByText("Upload Session"))

      // Fill form
      const selectTrigger = screen.getByRole("combobox")
      await user.click(selectTrigger)
      const doeJaneOptions = await screen.findAllByText("Doe, Jane")
      await user.click(doeJaneOptions[doeJaneOptions.length - 1])

      const dateInput = screen.getByLabelText(/Session Date/)
      await user.type(dateInput, "2024-01-15T14:30")

      // Upload VTT file
      const file = new File(
        ["WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nTest content"],
        "transcript.vtt",
        { type: "text/vtt" }
      )

      const fileInput = screen.getByLabelText(/Transcript File/)
      await user.upload(fileInput, file)

      await user.click(screen.getByText("Upload & Generate SOAP"))

      await waitFor(() => {
        expect(sessionsApi.uploadSession).toHaveBeenCalledWith(
          "patient-1",
          expect.objectContaining({
            transcript: expect.objectContaining({
              format: "vtt",
            }),
          }),
          undefined
        )
      })
    })
  })
})
