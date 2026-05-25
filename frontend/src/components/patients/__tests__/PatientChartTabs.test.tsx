// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PatientChartTabs shell tests (PABLO-6x5.2)
 *
 * Covers the default tab, switching between Notes/Documents, and the
 * count badges wired to the list hooks. Bodies are placeholders only —
 * the Notes preview (6x5.4) and Documents move (6x5.5) fill them in.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { PatientChartTabs } from "../PatientChartTabs"
import * as notesApi from "@/lib/api/notes"
import * as documentsApi from "@/lib/api/patientDocuments"

vi.mock("@/lib/api/notes")
vi.mock("@/lib/api/patientDocuments")

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "ChartTabsWrapper"
  return Wrapper
}

describe("PatientChartTabs", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(notesApi.listNotesForPatient).mockResolvedValue({
      data: [],
      total: 2,
    })
    vi.mocked(documentsApi.listPatientDocuments).mockResolvedValue({
      data: [],
      total: 1,
    })
  })

  it("defaults to the Notes tab", () => {
    render(<PatientChartTabs patientId="p1" />, { wrapper: createWrapper() })

    const notesTab = screen.getByRole("tab", { name: /notes/i })
    expect(notesTab).toHaveAttribute("data-state", "active")
    expect(screen.getByText(/no notes yet|notes? on file/i)).toBeInTheDocument()
  })

  it("switches to the Documents tab on click", async () => {
    const user = userEvent.setup()
    render(<PatientChartTabs patientId="p1" />, { wrapper: createWrapper() })

    await user.click(screen.getByRole("tab", { name: /documents/i }))

    expect(screen.getByRole("tab", { name: /documents/i })).toHaveAttribute(
      "data-state",
      "active",
    )
    expect(screen.getByText(/document.* on file/i)).toBeInTheDocument()
  })

  it("shows count badges sourced from the list hooks", async () => {
    render(<PatientChartTabs patientId="p1" />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText("2")).toBeInTheDocument()
      expect(screen.getByText("1")).toBeInTheDocument()
    })
  })
})
