// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PatientChartTabs tests (PABLO-6x5.2 shell + PABLO-6x5.4 notes preview)
 *
 * Covers the default tab, tab switching, the count badges wired to the
 * list hooks, and the Notes-tab preview: 3-most-recent rows with type and
 * draft/finalized badges, click-through routing, and the empty state.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { PatientChartTabs } from "../PatientChartTabs"
import { ToastProvider } from "@/components/ui/Toast"
import { createMockNote } from "@/test/factories"
import * as notesApi from "@/lib/api/notes"
import * as documentsApi from "@/lib/api/patientDocuments"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock("@/lib/api/notes")
vi.mock("@/lib/api/patientDocuments")

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  )
  Wrapper.displayName = "ChartTabsWrapper"
  return Wrapper
}

describe("PatientChartTabs", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(notesApi.listNotesForPatient).mockResolvedValue({
      data: [
        createMockNote({
          id: "n1",
          note_type: "soap",
          session_id: "s1",
          finalized_at: "2024-02-01T10:00:00Z",
        }),
        createMockNote({
          id: "n2",
          note_type: "narrative",
          session_id: null,
          finalized_at: null,
        }),
      ],
      total: 2,
    })
    vi.mocked(documentsApi.listPatientDocuments).mockResolvedValue({
      data: [],
      total: 1,
    })
  })

  it("defaults to the Notes tab and previews recent notes", async () => {
    render(<PatientChartTabs patientId="p1" />, { wrapper: createWrapper() })

    const notesTab = screen.getByRole("tab", { name: /notes/i })
    expect(notesTab).toHaveAttribute("data-state", "active")

    await waitFor(() => {
      expect(screen.getByText("soap")).toBeInTheDocument()
    })
    expect(screen.getByText("narrative")).toBeInTheDocument()
    expect(screen.getByText("Finalized")).toBeInTheDocument()
    expect(screen.getByText("Draft")).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: /view all notes/i }),
    ).toHaveAttribute("href", "/dashboard/patients/p1/notes")
  })

  it("routes session notes to the session and standalone notes to their edit page", async () => {
    render(<PatientChartTabs patientId="p1" />, { wrapper: createWrapper() })

    const soapRow = await screen.findByText("soap")
    expect(soapRow.closest("a")).toHaveAttribute(
      "href",
      "/dashboard/sessions/s1",
    )
    expect(screen.getByText("narrative").closest("a")).toHaveAttribute(
      "href",
      "/dashboard/patients/p1/notes/n2",
    )
  })

  it("shows an empty state when the patient has no notes", async () => {
    vi.mocked(notesApi.listNotesForPatient).mockResolvedValue({
      data: [],
      total: 0,
    })
    render(<PatientChartTabs patientId="p1" />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText(/no notes yet/i)).toBeInTheDocument()
    })
  })

  it("switches to the Documents tab and mounts the documents panel", async () => {
    const user = userEvent.setup()
    render(<PatientChartTabs patientId="p1" />, { wrapper: createWrapper() })

    await user.click(screen.getByRole("tab", { name: /documents/i }))

    expect(screen.getByRole("tab", { name: /documents/i })).toHaveAttribute(
      "data-state",
      "active",
    )
    // PatientDocuments renders its upload control; the mocked list is
    // empty so it falls through to the empty state.
    expect(
      screen.getByRole("button", { name: /upload document/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(/no documents uploaded yet/i)).toBeInTheDocument()
  })

  it("shows count badges sourced from the list hooks", async () => {
    render(<PatientChartTabs patientId="p1" />, { wrapper: createWrapper() })

    const notesTab = screen.getByRole("tab", { name: /notes/i })
    const documentsTab = screen.getByRole("tab", { name: /documents/i })
    await waitFor(() => {
      expect(within(notesTab).getByText("2")).toBeInTheDocument()
      expect(within(documentsTab).getByText("1")).toBeInTheDocument()
    })
  })
})
