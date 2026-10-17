// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SuperbillDialog tests — the superbill from the client's chart.
 *
 * Pins what a clinician relies on: the period opens on last month, the
 * diagnosis setting reads and saves the client's own value, a refusal shows
 * every missing item rather than a failed download, and dates the route would
 * reject are caught before the request goes out.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { lastMonth, SuperbillDialog } from "../SuperbillDialog"
import { SuperbillRefusedError } from "@/lib/api/superbills"

const mockFetchSuperbill = vi.fn()
const mockUpdatePatient = vi.fn()
const mockPatient = vi.fn()
const mockOnOpenChange = vi.fn()

vi.mock("@/hooks/usePatients", () => ({
  usePatient: () => mockPatient(),
  useUpdatePatient: () => ({ mutateAsync: mockUpdatePatient, isPending: false }),
}))

vi.mock("@/lib/api/superbills", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/superbills")>()
  return { ...actual, fetchSuperbill: (...args: unknown[]) => mockFetchSuperbill(...args) }
})

const TODAY = new Date(2026, 4, 15) // 15 May 2026

function setup(includesDiagnosis = false) {
  mockPatient.mockReturnValue({ data: { superbill_includes_diagnosis: includesDiagnosis } })
  render(
    <SuperbillDialog
      patientId="patient-1"
      open
      onOpenChange={mockOnOpenChange}
      today={TODAY}
    />,
  )
}

describe("lastMonth", () => {
  it("is the whole of the month before", () => {
    expect(lastMonth(TODAY)).toEqual({ start: "2026-04-01", end: "2026-04-30" })
  })

  it("crosses the year in January", () => {
    expect(lastMonth(new Date(2027, 0, 9))).toEqual({ start: "2026-12-01", end: "2026-12-31" })
  })
})

describe("SuperbillDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    URL.createObjectURL = vi.fn(() => "blob:superbill")
    URL.revokeObjectURL = vi.fn()
  })

  it("asks for last month's superbill", async () => {
    mockFetchSuperbill.mockResolvedValue(new Blob(["%PDF"]))
    setup()

    await userEvent.click(screen.getByRole("button", { name: /Download PDF/ }))

    expect(mockFetchSuperbill).toHaveBeenCalledWith("patient-1", "2026-04-01", "2026-04-30")
    await waitFor(() => expect(mockOnOpenChange).toHaveBeenCalledWith(false))
  })

  it("shows the client's diagnosis setting as it is saved", () => {
    setup(true)

    expect(screen.getByLabelText("Include the diagnosis")).toBeChecked()
  })

  it("saves the diagnosis setting on the client the moment it changes", async () => {
    mockUpdatePatient.mockResolvedValue({})
    setup(false)

    await userEvent.click(screen.getByLabelText("Include the diagnosis"))

    expect(mockUpdatePatient).toHaveBeenCalledWith({
      patientId: "patient-1",
      data: { superbill_includes_diagnosis: true },
    })
  })

  it("says so when the setting could not be saved", async () => {
    mockUpdatePatient.mockRejectedValue(new Error("boom"))
    setup(false)

    await userEvent.click(screen.getByLabelText("Include the diagnosis"))

    expect(await screen.findByRole("alert")).toHaveTextContent("could not be saved")
  })

  it("lists every missing item when the superbill is refused", async () => {
    mockFetchSuperbill.mockRejectedValue(
      new SuperbillRefusedError("missing", [
        {
          severity: "blocking",
          code: "missing_field",
          message: "The service on 2026-04-15 has no service code. Add it to the appointment.",
          field: "lines[0].cpt",
        },
        {
          severity: "blocking",
          code: "missing_field",
          message: "rendering_provider.npi is required.",
          field: "rendering_provider.npi",
        },
      ]),
    )
    setup()

    await userEvent.click(screen.getByRole("button", { name: /Download PDF/ }))

    const list = await screen.findByRole("list", { name: "What is missing" })
    expect(list).toHaveTextContent("has no service code")
    expect(list).toHaveTextContent("rendering_provider.npi is required.")
    expect(mockOnOpenChange).not.toHaveBeenCalledWith(false)
  })

  it("catches an end date before the start without asking the server", async () => {
    setup()

    const to = screen.getByLabelText("To")
    await userEvent.clear(to)
    await userEvent.type(to, "2026-03-01")
    await userEvent.click(screen.getByRole("button", { name: /Download PDF/ }))

    expect(screen.getByRole("alert")).toHaveTextContent("The end date is before the start date.")
    expect(mockFetchSuperbill).not.toHaveBeenCalled()
  })
})
