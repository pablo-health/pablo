// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * AwaitingReviewPanel Component Tests
 *
 * Covers: the inline rows the server returns link to their session detail,
 * the panel is absent when empty or loading, and the "View all" overflow link
 * reflects the full total (which may exceed the inline rows).
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { AwaitingReviewPanel } from "../AwaitingReviewPanel"
import type { AwaitingReviewItem } from "@/types/dashboard"

const useDashboardSummary = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useDashboard", () => ({
  useDashboardSummary: (...args: unknown[]) => useDashboardSummary(...args),
}))

vi.mock("@/hooks/usePreferences", () => ({
  useUserTimeZone: () => "America/New_York",
  formatInUserTimeZone: (
    date: Date | string,
    timeZone: string,
    options: Intl.DateTimeFormatOptions,
  ) => new Date(date).toLocaleDateString("en-US", { ...options, timeZone }),
}))

// Stub the status badge — it polls via useSession, which we don't mock here.
vi.mock("@/components/sessions/SessionStatusBadge", () => ({
  SessionStatusBadge: ({ status }: { status: string }) => <span>{status}</span>,
}))

function item(overrides: Partial<AwaitingReviewItem>): AwaitingReviewItem {
  return {
    session_id: "s1",
    patient_name: "Patient",
    session_date: "2026-05-10T12:00:00Z",
    status: "pending_review",
    note_finalized_at: null,
    ...overrides,
  }
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <AwaitingReviewPanel />
    </QueryClientProvider>,
  )
}

describe("AwaitingReviewPanel", () => {
  it("renders nothing while loading", () => {
    useDashboardSummary.mockReturnValue({ data: undefined, isLoading: true })
    const { container } = renderPanel()
    expect(container).toBeEmptyDOMElement()
  })

  it("renders nothing when nothing awaits review", () => {
    useDashboardSummary.mockReturnValue({
      data: { awaiting_review: [], awaiting_review_total: 0 },
      isLoading: false,
    })
    const { container } = renderPanel()
    expect(container).toBeEmptyDOMElement()
  })

  it("lists the returned rows, each linking to its session detail", () => {
    useDashboardSummary.mockReturnValue({
      data: {
        awaiting_review: [
          item({ session_id: "pending-1", patient_name: "Doe, Jane" }),
        ],
        awaiting_review_total: 1,
      },
      isLoading: false,
    })

    renderPanel()

    expect(screen.getByText("Notes awaiting review")).toBeInTheDocument()
    const link = screen.getByRole("link", { name: /Doe, Jane/ })
    expect(link).toHaveAttribute("href", "/dashboard/sessions/pending-1")
  })

  it("shows a View all overflow link when the total exceeds the inline rows", () => {
    const rows = Array.from({ length: 5 }, (_, i) =>
      item({ session_id: `pending-${i}`, patient_name: `Patient ${i}` }),
    )
    useDashboardSummary.mockReturnValue({
      data: { awaiting_review: rows, awaiting_review_total: 7 },
      isLoading: false,
    })

    renderPanel()

    const reviewLinks = screen
      .getAllByRole("link")
      .filter((el) => el.getAttribute("href")?.startsWith("/dashboard/sessions"))
    const rowLinks = reviewLinks.filter(
      (el) => el.getAttribute("href") !== "/dashboard/sessions",
    )
    expect(rowLinks).toHaveLength(5)

    const viewAll = screen.getByRole("link", { name: /view all 7/i })
    expect(viewAll).toHaveAttribute("href", "/dashboard/sessions")
  })
})
