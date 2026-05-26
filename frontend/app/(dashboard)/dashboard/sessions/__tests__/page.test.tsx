// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Tests for Review Worklist Page
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"

import ReviewPage from "../page"
import type { SessionResponse } from "@/types/sessions"

// Capture the props the page hands to SessionsTable so we can assert on the
// review-status filter without standing up the real data layer.
let capturedFilter: ((s: SessionResponse) => boolean) | undefined

vi.mock("@/components/sessions/SessionsTable", () => ({
  SessionsTable: ({ filter }: { filter?: (s: SessionResponse) => boolean }) => {
    capturedFilter = filter
    return <div data-testid="sessions-table">Sessions Table Component</div>
  },
}))

function sessionWithStatus(status: SessionResponse["status"]): SessionResponse {
  return { status } as SessionResponse
}

describe("ReviewPage", () => {
  describe("Page Structure", () => {
    it("renders the Review heading", () => {
      render(<ReviewPage />)

      const heading = screen.getByRole("heading", { name: "Review" })
      expect(heading).toBeInTheDocument()
      expect(heading.tagName).toBe("H1")
    })

    it("describes the worklist purpose", () => {
      render(<ReviewPage />)

      expect(
        screen.getByText(/waiting for your review before they're finalized/i),
      ).toBeInTheDocument()
    })

    it("renders SessionsTable", () => {
      render(<ReviewPage />)

      expect(screen.getByTestId("sessions-table")).toBeInTheDocument()
    })
  })

  describe("Review filter", () => {
    it("keeps sessions that still need attention", () => {
      render(<ReviewPage />)

      expect(capturedFilter).toBeDefined()
      expect(capturedFilter!(sessionWithStatus("pending_review"))).toBe(true)
      expect(capturedFilter!(sessionWithStatus("processing"))).toBe(true)
      expect(capturedFilter!(sessionWithStatus("queued"))).toBe(true)
      expect(capturedFilter!(sessionWithStatus("failed"))).toBe(true)
    })

    it("excludes terminal and not-yet-recorded sessions", () => {
      render(<ReviewPage />)

      expect(capturedFilter!(sessionWithStatus("finalized"))).toBe(false)
      expect(capturedFilter!(sessionWithStatus("cancelled"))).toBe(false)
      expect(capturedFilter!(sessionWithStatus("scheduled"))).toBe(false)
    })
  })
})
