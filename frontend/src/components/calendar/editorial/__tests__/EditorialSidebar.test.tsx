// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * EditorialSidebar Component Tests
 *
 * Covers the "New appointment" button and status filter controls, and —
 * the main focus — that read-only deployment mode hides the "New
 * appointment" button while the mini-month and status filters (read-only
 * affordances) stay usable.
 */

import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { EditorialSidebar } from "../EditorialSidebar"

function renderSidebar(overrides: Partial<Parameters<typeof EditorialSidebar>[0]> = {}) {
  return render(
    <EditorialSidebar
      selected={new Date("2026-06-15T00:00:00")}
      statusFilters={new Set(["confirmed"])}
      onSelectDate={vi.fn()}
      onCreateNew={vi.fn()}
      onToggleStatus={vi.fn()}
      {...overrides}
    />,
  )
}

describe("EditorialSidebar", () => {
  it("renders the New appointment button and calls onCreateNew on click", () => {
    const onCreateNew = vi.fn()
    renderSidebar({ onCreateNew })

    const button = screen.getByRole("button", { name: /new appointment/i })
    fireEvent.click(button)

    expect(onCreateNew).toHaveBeenCalledTimes(1)
  })

  it("renders the status filter checkboxes and the mini-month", () => {
    renderSidebar()

    expect(screen.getByText("Confirmed")).toBeInTheDocument()
    expect(screen.getByText("Completed")).toBeInTheDocument()
    expect(screen.getByText("Cancelled")).toBeInTheDocument()
    expect(screen.getByText("No-shows")).toBeInTheDocument()
    // Mini-month renders the browsed month heading.
    expect(screen.getByText("June 2026")).toBeInTheDocument()
  })

  describe("read-only deployment mode", () => {
    afterEach(() => vi.unstubAllEnvs())

    it("hides the New appointment button when read-only, but keeps the mini-month and status filters", () => {
      vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")

      renderSidebar()

      expect(
        screen.queryByRole("button", { name: /new appointment/i }),
      ).not.toBeInTheDocument()
      expect(screen.getByText("June 2026")).toBeInTheDocument()
      expect(screen.getByText("Confirmed")).toBeInTheDocument()
      expect(screen.getByText("Completed")).toBeInTheDocument()
      expect(screen.getByText("Cancelled")).toBeInTheDocument()
      expect(screen.getByText("No-shows")).toBeInTheDocument()
    })

    it("toggling a status filter still works when read-only", () => {
      vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")
      const onToggleStatus = vi.fn()

      renderSidebar({ onToggleStatus })
      fireEvent.click(screen.getByText("Completed"))

      expect(onToggleStatus).toHaveBeenCalledWith("completed")
    })

    it("shows the New appointment button when the deployment flag is unset", () => {
      renderSidebar()

      expect(
        screen.getByRole("button", { name: /new appointment/i }),
      ).toBeInTheDocument()
    })
  })
})
