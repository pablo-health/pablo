// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Tests for Sessions List Page
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"

import SessionsPage from "../page"

// Mock child components
vi.mock("@/components/sessions/SessionsTable", () => ({
  SessionsTable: () => (
    <div data-testid="sessions-table">
      Sessions Table Component
    </div>
  ),
}))

vi.mock("@/components/sessions/UploadTranscriptDialog", () => ({
  UploadTranscriptDialog: ({ trigger }: any) => (
    <div data-testid="upload-dialog-wrapper">
      {trigger}
    </div>
  ),
}))

describe("SessionsPage", () => {
  describe("Page Structure", () => {
    it("renders page header", () => {
      render(<SessionsPage />)

      expect(screen.getByRole("heading", { name: "Sessions" })).toBeInTheDocument()
      expect(screen.getByText("View and manage therapy sessions")).toBeInTheDocument()
    })

    it("renders upload button", () => {
      render(<SessionsPage />)

      expect(screen.getByRole("button", { name: /upload session/i })).toBeInTheDocument()
    })

    it("renders SessionsTable component", () => {
      render(<SessionsPage />)

      expect(screen.getByTestId("sessions-table")).toBeInTheDocument()
    })

    it("has proper heading hierarchy", () => {
      render(<SessionsPage />)

      const heading = screen.getByRole("heading", { name: "Sessions" })
      expect(heading.tagName).toBe("H1")
    })

    it("renders upload dialog wrapper", () => {
      render(<SessionsPage />)

      expect(screen.getByTestId("upload-dialog-wrapper")).toBeInTheDocument()
    })
  })

  describe("Layout", () => {
    it("renders header and table sections", () => {
      render(<SessionsPage />)

      // Header section
      expect(screen.getByText("Sessions")).toBeInTheDocument()
      expect(screen.getByText("View and manage therapy sessions")).toBeInTheDocument()

      // Upload button
      expect(screen.getByRole("button", { name: /upload session/i })).toBeInTheDocument()

      // Table section (delegated to SessionsTable component)
      expect(screen.getByTestId("sessions-table")).toBeInTheDocument()
    })
  })

  describe("Component Integration", () => {
    it("passes trigger prop to UploadTranscriptDialog", () => {
      render(<SessionsPage />)

      // The upload button should be rendered inside the upload dialog wrapper
      const uploadButton = screen.getByRole("button", { name: /upload session/i })
      const dialogWrapper = screen.getByTestId("upload-dialog-wrapper")

      expect(dialogWrapper).toContainElement(uploadButton)
    })

    it("renders SessionsTable which handles data loading internally", () => {
      render(<SessionsPage />)

      // SessionsTable is responsible for handling its own loading/error states
      expect(screen.getByTestId("sessions-table")).toBeInTheDocument()
    })
  })

  describe("Accessibility", () => {
    it("has accessible button for uploading sessions", () => {
      render(<SessionsPage />)

      const uploadButton = screen.getByRole("button", { name: /upload session/i })
      expect(uploadButton).toBeInTheDocument()
    })

    it("has proper heading structure", () => {
      render(<SessionsPage />)

      const heading = screen.getByRole("heading", { level: 1 })
      expect(heading).toHaveTextContent("Sessions")
    })
  })
})
