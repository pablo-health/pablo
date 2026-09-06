// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * BillerExport Component Tests
 *
 * Covers what the export card has to get right: asking for the range the
 * inputs hold, handing the browser the file to save, listing every blocked
 * claim and its findings when the export is refused, and a plain failure
 * message for anything else.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { ApiError } from "@/lib/api/client"
import { BillerExport } from "../BillerExport"

const downloadClaimsCsv = vi.hoisted(() => vi.fn())

vi.mock("@/lib/api/claims", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/claims")>("@/lib/api/claims")
  return {
    ...actual,
    downloadClaimsCsv: (...args: unknown[]) => downloadClaimsCsv(...args),
  }
})

describe("BillerExport", () => {
  const clicked = vi.fn()

  beforeEach(() => {
    downloadClaimsCsv.mockReset()
    clicked.mockReset()
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:claims"),
      revokeObjectURL: vi.fn(),
    })
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(clicked)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("downloads the CSV for the range in the inputs", async () => {
    downloadClaimsCsv.mockResolvedValue(new Blob(["control_number\n"], { type: "text/csv" }))
    render(<BillerExport />)

    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-09-01" } })
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-09-30" } })
    fireEvent.click(screen.getByRole("button", { name: "Export for biller" }))

    await waitFor(() => expect(clicked).toHaveBeenCalledTimes(1))
    expect(downloadClaimsCsv).toHaveBeenCalledWith("2026-09-01", "2026-09-30")
  })

  it("lists every blocked claim and its findings when the export is refused", async () => {
    downloadClaimsCsv.mockRejectedValue(
      new ApiError(
        "CLAIM_EXPORT_BLOCKED",
        "Some claims have blocking findings; nothing was exported.",
        {
          claims: [
            {
              claim_id: "c1",
              control_number: "88659891",
              findings: [
                {
                  severity: "blocking",
                  code: "dx_not_specific",
                  message: "Diagnosis 1 is not a billable code.",
                  field: "diagnosis_codes[0]",
                },
              ],
            },
          ],
        },
        422,
      ),
    )
    render(<BillerExport />)
    fireEvent.click(screen.getByRole("button", { name: "Export for biller" }))

    expect(await screen.findByText("Claim 88659891")).toBeInTheDocument()
    expect(screen.getByText("Diagnosis 1 is not a billable code.")).toBeInTheDocument()
    expect(screen.getByRole("alert")).toHaveTextContent("Nothing was exported")
    expect(clicked).not.toHaveBeenCalled()
  })

  it("shows a plain failure for any other error", async () => {
    downloadClaimsCsv.mockRejectedValue(new ApiError("INTERNAL_ERROR", "boom", undefined, 500))
    render(<BillerExport />)
    fireEvent.click(screen.getByRole("button", { name: "Export for biller" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("could not be prepared")
  })

  it("will not export a range that ends before it starts", () => {
    render(<BillerExport />)
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-09-30" } })
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-09-01" } })
    expect(screen.getByRole("button", { name: "Export for biller" })).toBeDisabled()
  })
})
