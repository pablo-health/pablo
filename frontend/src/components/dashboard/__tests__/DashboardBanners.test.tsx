// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { DashboardBanners } from "../DashboardBanners"
import { createMockNote, createMockSession } from "@/test/factories"

const useSessionList = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useSessions", () => ({
  useSessionList: (...args: unknown[]) => useSessionList(...args),
}))

function renderBanners() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <DashboardBanners />
    </QueryClientProvider>,
  )
}

describe("DashboardBanners", () => {
  it("renders nothing when no notes are pending", () => {
    useSessionList.mockReturnValue({ data: { data: [] } })

    const { container } = renderBanners()

    expect(container).toBeEmptyDOMElement()
  })

  it("renders singular phrasing for one pending note", () => {
    useSessionList.mockReturnValue({
      data: {
        data: [
          createMockSession({
            id: "s1",
            note: createMockNote({ finalized_at: null }),
          }),
        ],
      },
    })

    renderBanners()

    expect(
      screen.getByText("1 note awaiting your signature"),
    ).toBeInTheDocument()
  })

  it("renders plural phrasing for multiple pending notes and links to sessions", () => {
    useSessionList.mockReturnValue({
      data: {
        data: [
          createMockSession({
            id: "s1",
            note: createMockNote({ finalized_at: null }),
          }),
          createMockSession({
            id: "s2",
            note: createMockNote({ finalized_at: null }),
          }),
          createMockSession({
            id: "s3",
            note: createMockNote({ finalized_at: "2026-04-01T00:00:00Z" }),
          }),
        ],
      },
    })

    renderBanners()

    const banner = screen.getByRole("link", { name: /2 notes awaiting/i })
    expect(banner).toHaveAttribute("href", "/dashboard/sessions")
  })
})
