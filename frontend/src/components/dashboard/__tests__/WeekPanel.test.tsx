// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { WeekPanel } from "../WeekPanel"
import { createMockNote, createMockSession } from "@/test/factories"

const useAppointmentList = vi.hoisted(() => vi.fn())
const useSessionList = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useAppointments", () => ({
  useAppointmentList: (...args: unknown[]) => useAppointmentList(...args),
}))

vi.mock("@/hooks/useSessions", () => ({
  useSessionList: (...args: unknown[]) => useSessionList(...args),
}))

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <WeekPanel />
    </QueryClientProvider>,
  )
}

describe("WeekPanel", () => {
  it("counts only sessions where a note exists but isn't finalized", () => {
    useAppointmentList.mockReturnValue({ data: { data: [] } })
    useSessionList.mockReturnValue({
      data: {
        data: [
          // counts: has a draft note
          createMockSession({
            id: "s1",
            note: createMockNote({ finalized_at: null }),
          }),
          // skipped: note already finalized
          createMockSession({
            id: "s2",
            note: createMockNote({ finalized_at: "2026-05-01T00:00:00Z" }),
          }),
          // skipped: no note yet (still recording)
          createMockSession({ id: "s3", note: null }),
        ],
      },
      isLoading: false,
    })

    renderPanel()

    const notesRow = screen
      .getByText(/notes awaiting your signature/i)
      .closest("a")
    expect(notesRow).toHaveTextContent("1")
    expect(notesRow).toHaveAttribute("href", "/dashboard/sessions")
  })

  it("counts queued and processing transcripts", () => {
    useAppointmentList.mockReturnValue({ data: { data: [] } })
    useSessionList.mockReturnValue({
      data: {
        data: [
          createMockSession({ id: "s1", status: "queued" }),
          createMockSession({ id: "s2", status: "processing" }),
          createMockSession({ id: "s3", status: "finalized" }),
        ],
      },
      isLoading: false,
    })

    renderPanel()

    const transcriptRow = screen
      .getByText(/transcripts still processing/i)
      .closest("a")
    expect(transcriptRow).toHaveTextContent("2")
  })

  it("counts only confirmed upcoming appointments", () => {
    useAppointmentList.mockReturnValue({
      data: {
        data: [
          { status: "confirmed", id: "a1" },
          { status: "confirmed", id: "a2" },
          { status: "cancelled", id: "a3" },
          { status: "completed", id: "a4" },
        ],
      },
    })
    useSessionList.mockReturnValue({
      data: { data: [] },
      isLoading: false,
    })

    renderPanel()

    const upcomingRow = screen
      .getByText(/upcoming sessions/i)
      .closest("a")
    expect(upcomingRow).toHaveTextContent("2")
    expect(upcomingRow).toHaveAttribute("href", "/dashboard/calendar")
  })

  it("renders em-dash placeholders while sessions are loading", () => {
    useAppointmentList.mockReturnValue({ data: { data: [] } })
    useSessionList.mockReturnValue({ data: undefined, isLoading: true })

    renderPanel()

    const notesRow = screen
      .getByText(/notes awaiting your signature/i)
      .closest("a")
    expect(notesRow).toHaveTextContent("—")
  })
})
