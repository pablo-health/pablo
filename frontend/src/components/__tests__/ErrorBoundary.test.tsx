// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { ErrorBoundary } from "../ErrorBoundary"

function ThrowingChild({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) {
    throw new Error("Test render error")
  }
  return <div>Child content</div>
}

describe("ErrorBoundary", () => {
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    // Suppress React's error boundary console output during tests
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {})
  })

  afterEach(() => {
    consoleErrorSpy.mockRestore()
  })

  describe("Happy Path", () => {
    it("renders children when no error occurs", () => {
      render(
        <ErrorBoundary>
          <div>Normal content</div>
        </ErrorBoundary>
      )

      expect(screen.getByText("Normal content")).toBeInTheDocument()
    })
  })

  describe("Error Handling", () => {
    it("shows fallback UI when a child throws", () => {
      render(
        <ErrorBoundary>
          <ThrowingChild shouldThrow={true} />
        </ErrorBoundary>
      )

      expect(screen.getByText("Something went wrong")).toBeInTheDocument()
      expect(
        screen.getByText("An unexpected error occurred. Please try again.")
      ).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument()
    })

    it("does not show child content after error", () => {
      render(
        <ErrorBoundary>
          <ThrowingChild shouldThrow={true} />
        </ErrorBoundary>
      )

      expect(screen.queryByText("Child content")).not.toBeInTheDocument()
    })

    it("renders custom fallback when provided", () => {
      render(
        <ErrorBoundary fallback={<div>Custom error UI</div>}>
          <ThrowingChild shouldThrow={true} />
        </ErrorBoundary>
      )

      expect(screen.getByText("Custom error UI")).toBeInTheDocument()
      expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument()
    })

    it("has role=alert on default fallback for accessibility", () => {
      render(
        <ErrorBoundary>
          <ThrowingChild shouldThrow={true} />
        </ErrorBoundary>
      )

      expect(screen.getByRole("alert")).toBeInTheDocument()
    })
  })

  describe("PHI Safety", () => {
    it("logs only error name, not message or stack", () => {
      render(
        <ErrorBoundary>
          <ThrowingChild shouldThrow={true} />
        </ErrorBoundary>
      )

      // Find our specific log call (not React's internal error logging)
      const ourLogCall = consoleErrorSpy.mock.calls.find(
        (call: unknown[]) => call[0] === "ErrorBoundary caught:"
      )
      expect(ourLogCall).toBeDefined()
      expect(ourLogCall![1]).toBe("Error")
      // Ensure the error message is NOT logged
      expect(ourLogCall).not.toContain("Test render error")
    })
  })

  describe("Recovery", () => {
    it("resets and re-renders children when Try again is clicked", () => {
      let shouldThrow = true

      function ConditionalThrower() {
        if (shouldThrow) throw new Error("Boom")
        return <div>Recovered content</div>
      }

      render(
        <ErrorBoundary>
          <ConditionalThrower />
        </ErrorBoundary>
      )

      expect(screen.getByText("Something went wrong")).toBeInTheDocument()

      // Fix the error condition before clicking Try again
      shouldThrow = false
      fireEvent.click(screen.getByRole("button", { name: "Try again" }))

      expect(screen.getByText("Recovered content")).toBeInTheDocument()
      expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument()
    })
  })
})
