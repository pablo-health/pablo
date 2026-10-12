// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { _resetSupportEmailLookupForTests } from "@/lib/support"

const reportFrontendError = vi.fn()

vi.mock("@/lib/feErrorReporter", () => ({
  reportFrontendError: (...args: unknown[]) => reportFrontendError(...args),
}))

import { ErrorScreen } from "../ErrorScreen"

describe("ErrorScreen", () => {
  beforeEach(() => {
    reportFrontendError.mockReset().mockResolvedValue(undefined)
    _resetSupportEmailLookupForTests()
    vi.spyOn(console, "error").mockImplementation(() => {})
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ supportEmail: "help@clinic.example" }),
      }),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  // A crash caught by a React boundary never reaches window.onerror, so the
  // boundary itself is the only place it can be reported from.
  it("reports the caught error", () => {
    const error = new TypeError("boom")
    render(<ErrorScreen error={error} onRetry={() => {}} logLabel="Application error" />)

    expect(reportFrontendError).toHaveBeenCalledWith(error)
  })

  // The root boundary renders with no providers at all.
  it("shows the support address without any app provider", async () => {
    render(<ErrorScreen error={new Error("boom")} onRetry={() => {}} logLabel="Global" />)

    expect(await screen.findByRole("link", { name: "help@clinic.example" })).toHaveAttribute(
      "href",
      "mailto:help@clinic.example",
    )
  })

  it("retries through the handler it was given", () => {
    const onRetry = vi.fn()
    render(<ErrorScreen error={new Error("boom")} onRetry={onRetry} logLabel="Global" />)

    fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(onRetry).toHaveBeenCalled()
  })

  it("shows the reference a support request can quote", () => {
    const error = Object.assign(new Error("boom"), { digest: "abc123" })
    render(<ErrorScreen error={error} onRetry={() => {}} logLabel="Global" />)

    expect(screen.getByText("abc123")).toBeInTheDocument()
  })
})
