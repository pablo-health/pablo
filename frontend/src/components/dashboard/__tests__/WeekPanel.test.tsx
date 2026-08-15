// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { WeekPanel } from "../WeekPanel"

const useDashboardSummary = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useDashboard", () => ({
  useDashboardSummary: (...args: unknown[]) => useDashboardSummary(...args),
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
  it("renders the server-computed counts for each row", () => {
    useDashboardSummary.mockReturnValue({
      data: {
        notes_pending_count: 1,
        transcription_pending_count: 2,
        week_confirmed_count: 3,
      },
      isLoading: false,
    })

    renderPanel()

    const notesRow = screen
      .getByText(/notes awaiting your review/i)
      .closest("a")
    expect(notesRow).toHaveTextContent("1")
    expect(notesRow).toHaveAttribute("href", "/dashboard/sessions")

    const transcriptRow = screen
      .getByText(/transcripts still processing/i)
      .closest("a")
    expect(transcriptRow).toHaveTextContent("2")

    const upcomingRow = screen.getByText(/upcoming sessions/i).closest("a")
    expect(upcomingRow).toHaveTextContent("3")
    expect(upcomingRow).toHaveAttribute("href", "/dashboard/calendar")
  })

  it("renders em-dash placeholders while loading", () => {
    useDashboardSummary.mockReturnValue({ data: undefined, isLoading: true })

    renderPanel()

    const notesRow = screen
      .getByText(/notes awaiting your review/i)
      .closest("a")
    expect(notesRow).toHaveTextContent("—")
  })
})
