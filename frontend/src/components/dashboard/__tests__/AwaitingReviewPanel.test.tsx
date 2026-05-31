// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * AwaitingReviewPanel Component Tests
 *
 * Covers: only pending_review sessions surface, links target the session
 * detail, the panel is absent when empty or loading, and the list caps at
 * MAX_ROWS with a "View all" overflow link.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { AwaitingReviewPanel } from "../AwaitingReviewPanel"
import { createMockSession } from "@/test/factories"

const useSessionList = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useSessions", () => ({
  useSessionList: (...args: unknown[]) => useSessionList(...args),
}))

// Stub the status badge — it polls via useSession, which we don't mock here.
vi.mock("@/components/sessions/SessionStatusBadge", () => ({
  SessionStatusBadge: ({ status }: { status: string }) => <span>{status}</span>,
}))

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
    useSessionList.mockReturnValue({ data: undefined, isLoading: true })
    const { container } = renderPanel()
    expect(container).toBeEmptyDOMElement()
  })

  it("renders nothing when no session is pending review", () => {
    useSessionList.mockReturnValue({
      data: {
        data: [
          createMockSession({ id: "s1", status: "processing" }),
          createMockSession({ id: "s2", status: "finalized" }),
        ],
      },
      isLoading: false,
    })
    const { container } = renderPanel()
    expect(container).toBeEmptyDOMElement()
  })

  it("lists only pending_review sessions, each linking to its detail", () => {
    useSessionList.mockReturnValue({
      data: {
        data: [
          createMockSession({
            id: "pending-1",
            patient_name: "Doe, Jane",
            status: "pending_review",
          }),
          createMockSession({ id: "proc-1", status: "processing" }),
          createMockSession({ id: "final-1", status: "finalized" }),
        ],
      },
      isLoading: false,
    })

    renderPanel()

    expect(screen.getByText("Notes awaiting review")).toBeInTheDocument()
    const link = screen.getByRole("link", { name: /Doe, Jane/ })
    expect(link).toHaveAttribute("href", "/dashboard/sessions/pending-1")
    // The finalized/processing sessions are not surfaced here.
    expect(screen.queryByText(/final-1|proc-1/)).not.toBeInTheDocument()
  })

  it("caps the inline list and shows a View all overflow link", () => {
    const sessions = Array.from({ length: 7 }, (_, i) =>
      createMockSession({
        id: `pending-${i}`,
        patient_name: `Patient ${i}`,
        status: "pending_review",
        session_date: `2026-05-${10 + i}T12:00:00Z`,
      }),
    )
    useSessionList.mockReturnValue({
      data: { data: sessions },
      isLoading: false,
    })

    renderPanel()

    // 5 rows inline + the "View all" link = 6 links total.
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
