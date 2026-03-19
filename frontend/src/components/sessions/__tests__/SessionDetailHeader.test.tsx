// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Tests for SessionDetailHeader component
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { SessionDetailHeader } from "../SessionDetailHeader"
import type { SessionStatus } from "@/types/sessions"

// Mock the SessionStatusBadge component
vi.mock("../SessionStatusBadge", () => ({
  SessionStatusBadge: ({ status, sessionId }: { status: SessionStatus; sessionId: string }) => (
    <div data-testid="status-badge" data-status={status} data-session-id={sessionId}>
      {status}
    </div>
  ),
}))

describe("SessionDetailHeader", () => {
  const defaultProps = {
    patientName: "John Doe",
    sessionDate: "2024-01-15T10:00:00Z",
    sessionNumber: 3,
    status: "pending_review" as SessionStatus,
    sessionId: "session-123",
  }

  describe("Rendering", () => {
    it("renders patient name", () => {
      render(<SessionDetailHeader {...defaultProps} />)

      expect(screen.getByRole("heading", { name: "John Doe" })).toBeInTheDocument()
    })

    it("renders formatted session date", () => {
      render(<SessionDetailHeader {...defaultProps} />)

      // January 15, 2024 format
      expect(screen.getByText("January 15, 2024")).toBeInTheDocument()
    })

    it("renders session number", () => {
      render(<SessionDetailHeader {...defaultProps} />)

      expect(screen.getByText("Session #3")).toBeInTheDocument()
    })

    it("renders calendar icon", () => {
      const { container } = render(<SessionDetailHeader {...defaultProps} />)

      const icon = container.querySelector('svg')
      expect(icon).toBeInTheDocument()
    })

    it("renders SessionStatusBadge with correct props", () => {
      render(<SessionDetailHeader {...defaultProps} />)

      const badge = screen.getByTestId("status-badge")
      expect(badge).toHaveAttribute("data-status", "pending_review")
      expect(badge).toHaveAttribute("data-session-id", "session-123")
    })
  })

  describe("Date Formatting", () => {
    it("formats different dates correctly", () => {
      const { rerender } = render(
        <SessionDetailHeader {...defaultProps} sessionDate="2024-12-25T14:30:00Z" />
      )
      expect(screen.getByText("December 25, 2024")).toBeInTheDocument()

      rerender(<SessionDetailHeader {...defaultProps} sessionDate="2024-07-04T08:00:00Z" />)
      expect(screen.getByText("July 4, 2024")).toBeInTheDocument()
    })
  })

  describe("Different Session Numbers", () => {
    it("renders session number 1", () => {
      render(<SessionDetailHeader {...defaultProps} sessionNumber={1} />)
      expect(screen.getByText("Session #1")).toBeInTheDocument()
    })

    it("renders session number 10", () => {
      render(<SessionDetailHeader {...defaultProps} sessionNumber={10} />)
      expect(screen.getByText("Session #10")).toBeInTheDocument()
    })

    it("renders large session numbers", () => {
      render(<SessionDetailHeader {...defaultProps} sessionNumber={99} />)
      expect(screen.getByText("Session #99")).toBeInTheDocument()
    })
  })

  describe("Different Statuses", () => {
    const statuses: SessionStatus[] = [
      "queued",
      "processing",
      "pending_review",
      "finalized",
      "failed",
    ]

    statuses.forEach((status) => {
      it(`renders with ${status} status`, () => {
        render(<SessionDetailHeader {...defaultProps} status={status} />)

        const badge = screen.getByTestId("status-badge")
        expect(badge).toHaveAttribute("data-status", status)
      })
    })
  })

  describe("Layout Structure", () => {
    it("has proper heading hierarchy", () => {
      render(<SessionDetailHeader {...defaultProps} />)

      const heading = screen.getByRole("heading")
      expect(heading.tagName).toBe("H1")
    })

    it("contains metadata section with date and session number", () => {
      const { container } = render(<SessionDetailHeader {...defaultProps} />)

      // Check that date and session number are in the same container
      const metadataSection = container.querySelector('.flex.items-center.gap-2')
      expect(metadataSection).toBeInTheDocument()
      expect(metadataSection).toHaveTextContent("January 15, 2024")
      expect(metadataSection).toHaveTextContent("Session #3")
    })
  })

  describe("Patient Name Variations", () => {
    it("renders short names", () => {
      render(<SessionDetailHeader {...defaultProps} patientName="Li" />)
      expect(screen.getByRole("heading", { name: "Li" })).toBeInTheDocument()
    })

    it("renders long names", () => {
      const longName = "Dr. Elizabeth Alexandra Mary Windsor"
      render(<SessionDetailHeader {...defaultProps} patientName={longName} />)
      expect(screen.getByRole("heading", { name: longName })).toBeInTheDocument()
    })

    it("renders names with special characters", () => {
      render(<SessionDetailHeader {...defaultProps} patientName="María José O'Brien-Smith" />)
      expect(screen.getByRole("heading", { name: "María José O'Brien-Smith" })).toBeInTheDocument()
    })
  })
})
