// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { DashboardBanners } from "../DashboardBanners"

const useDashboardSummary = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useDashboard", () => ({
  useDashboardSummary: (...args: unknown[]) => useDashboardSummary(...args),
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
    useDashboardSummary.mockReturnValue({ data: { notes_pending_count: 0 } })

    const { container } = renderBanners()

    expect(container).toBeEmptyDOMElement()
  })

  it("renders singular phrasing for one pending note", () => {
    useDashboardSummary.mockReturnValue({ data: { notes_pending_count: 1 } })

    renderBanners()

    expect(
      screen.getByText("1 note awaiting your signature"),
    ).toBeInTheDocument()
  })

  it("renders plural phrasing for multiple pending notes and links to sessions", () => {
    useDashboardSummary.mockReturnValue({ data: { notes_pending_count: 2 } })

    renderBanners()

    const banner = screen.getByRole("link", { name: /2 notes awaiting/i })
    expect(banner).toHaveAttribute("href", "/dashboard/sessions")
  })
})
