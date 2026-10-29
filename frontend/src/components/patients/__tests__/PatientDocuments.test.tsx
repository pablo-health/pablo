// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PatientDocuments Component Tests
 *
 * Covers the document list rendering and the read-only deployment mode
 * gating of the upload / visibility-select / delete affordances. The
 * `usePatientDocuments` hooks are mocked so the list can be driven directly
 * without wiring react-query; `DocumentViewerSheet` is stubbed since it
 * isn't under test here.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import { PatientDocuments } from "../PatientDocuments"
import type { PatientDocumentResponse } from "@/types/patientDocuments"

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
  Wrapper.displayName = "PatientDocumentsWrapper"
  return Wrapper
}

const mockUsePatientDocuments = vi.fn()
const mockUseUploadPatientDocument = vi.fn()
const mockUseDeletePatientDocument = vi.fn()
const mockUsePatientDocument = vi.fn()

vi.mock("@/hooks/usePatientDocuments", () => ({
  EXTRACTION_POLL_TIMEOUT_TICKS: 40,
  usePatientDocuments: (...args: unknown[]) => mockUsePatientDocuments(...args),
  useUploadPatientDocument: (...args: unknown[]) =>
    mockUseUploadPatientDocument(...args),
  useDeletePatientDocument: (...args: unknown[]) =>
    mockUseDeletePatientDocument(...args),
  usePatientDocument: (...args: unknown[]) => mockUsePatientDocument(...args),
}))

vi.mock("@/components/patients/DocumentViewerSheet", () => ({
  DocumentViewerSheet: () => null,
}))

function makeDoc(
  overrides: Partial<PatientDocumentResponse> = {},
): PatientDocumentResponse {
  return {
    id: "doc-1",
    patient_id: "patient-1",
    filename: "intake.pdf",
    mime_type: "application/pdf",
    size_bytes: 1234,
    created_at: "2026-05-24T10:00:00Z",
    finalized_at: "2026-05-24T10:00:01Z",
    category: "chart",
    extracted_text: null,
    extraction_status: "complete",
    text_extraction_failed: false,
    uploaded_by: "clinician",
    ...overrides,
  }
}

describe("PatientDocuments", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUsePatientDocuments.mockReturnValue({
      data: { data: [makeDoc()], total: 1 },
      isLoading: false,
      error: null,
    })
    mockUseUploadPatientDocument.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      stage: "idle",
    })
    mockUseDeletePatientDocument.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      variables: undefined,
    })
    mockUsePatientDocument.mockReturnValue({ data: undefined, pollCount: 0 })
  })

  it("renders the document list", () => {
    render(<PatientDocuments patientId="patient-1" />, { wrapper: createWrapper() })

    expect(screen.getByText("intake.pdf")).toBeInTheDocument()
  })

  describe("read-only deployment mode", () => {
    afterEach(() => {
      vi.unstubAllEnvs()
    })

    it("hides upload, visibility select, and delete when read-only", () => {
      vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")
      render(<PatientDocuments patientId="patient-1" />, { wrapper: createWrapper() })

      expect(screen.getByText("intake.pdf")).toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: /upload document/i }),
      ).not.toBeInTheDocument()
      expect(
        screen.queryByTestId("patient-document-category-select"),
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: /^delete$/i }),
      ).not.toBeInTheDocument()
      expect(screen.getByRole("button", { name: /^view$/i })).toBeInTheDocument()
      expect(
        screen.getByRole("button", { name: /^download$/i }),
      ).toBeInTheDocument()
    })

    it("shows upload, visibility select, and delete when the flag is unset", () => {
      render(<PatientDocuments patientId="patient-1" />, { wrapper: createWrapper() })

      expect(
        screen.getByRole("button", { name: /upload document/i }),
      ).toBeInTheDocument()
      expect(
        screen.getByTestId("patient-document-category-select"),
      ).toBeInTheDocument()
      expect(screen.getByRole("button", { name: /^delete$/i })).toBeInTheDocument()
      expect(screen.getByRole("button", { name: /^view$/i })).toBeInTheDocument()
      expect(
        screen.getByRole("button", { name: /^download$/i }),
      ).toBeInTheDocument()
    })
  })

  describe("what the padlock means", () => {
    it("marks a file sent on a message, and does not call it private", () => {
      mockUsePatientDocuments.mockReturnValue({
        data: {
          data: [
            makeDoc({
              id: "doc-msg",
              filename: "insurance-card.png",
              category: "message",
              uploaded_by: "patient",
              message_thread_id: "t-1",
            }),
          ],
          total: 1,
        },
        isLoading: false,
        error: null,
      })
      render(<PatientDocuments patientId="patient-1" />, { wrapper: createWrapper() })

      expect(screen.getByText(/sent in a message/i)).toBeInTheDocument()
      expect(screen.getByTestId("patient-document-thread-doc-msg")).toBeInTheDocument()
      expect(
        screen.queryByLabelText(/only you can see this/i),
      ).not.toBeInTheDocument()
    })

    it("still locks the two categories only their uploader can see", () => {
      mockUsePatientDocuments.mockReturnValue({
        data: {
          data: [makeDoc({ id: "doc-notes", category: "psychotherapy_notes" })],
          total: 1,
        },
        isLoading: false,
        error: null,
      })
      render(<PatientDocuments patientId="patient-1" />, { wrapper: createWrapper() })

      expect(screen.getByLabelText(/only you can see this/i)).toBeInTheDocument()
    })
  })

  it("shows extraction progress until the document settles, then re-lists", () => {
    mockUseUploadPatientDocument.mockReturnValue({
      mutate: (_vars: unknown, options: { onSuccess: (doc: PatientDocumentResponse) => void }) =>
        options.onSuccess(makeDoc({ id: "doc-2", extraction_status: "pending" })),
      isPending: false,
      stage: "idle",
    })
    const queryClient = new QueryClient()
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { container, rerender } = render(<PatientDocuments patientId="patient-1" />, {
      wrapper,
    })

    fireEvent.change(container.querySelector('input[type="file"]')!, {
      target: { files: [new File(["x"], "scan.pdf", { type: "application/pdf" })] },
    })

    expect(screen.getByText("Extracting text…")).toBeInTheDocument()
    expect(mockUsePatientDocument).toHaveBeenLastCalledWith("doc-2")
    expect(invalidate).not.toHaveBeenCalled()

    mockUsePatientDocument.mockReturnValue({
      data: makeDoc({ id: "doc-2", extraction_status: "complete" }),
      pollCount: 2,
    })
    rerender(<PatientDocuments patientId="patient-1" />)

    expect(screen.queryByText("Extracting text…")).not.toBeInTheDocument()
    expect(invalidate).toHaveBeenCalledTimes(1)
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["patient-documents", "byPatient", "patient-1"],
    })
  })
})
