// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PatientExport: the chart's Export action asks the export endpoint for the
 * chosen format and options, and saves what comes back under the name the
 * server gave it. The archive is the default format; JSON and PDF stay
 * selectable. Transcripts and psychotherapy notes are opt-in.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { PatientExport, exportSummary } from "@/components/patients/PatientExport"

const mockDownload = vi.fn()
const mockSave = vi.fn()

vi.mock("@/lib/api/patients", () => ({
  downloadPatientExport: (...args: unknown[]) => mockDownload(...args),
}))

vi.mock("@/lib/saveFile", () => ({
  saveFile: (...args: unknown[]) => mockSave(...args),
}))

async function openDialog() {
  const user = userEvent.setup()
  render(<PatientExport patientId="patient-1" patientName="Maria Lopez" />)
  await user.click(screen.getByRole("button", { name: "Export" }))
  return user
}

describe("PatientExport", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("opens a dialog titled for the chart with both choices unchecked", async () => {
    await openDialog()

    expect(
      screen.getByRole("dialog", { name: "Export this chart" }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("checkbox", { name: "Include session transcripts" }),
    ).not.toBeChecked()
    expect(
      screen.getByRole("checkbox", { name: "Include psychotherapy notes" }),
    ).not.toBeChecked()
  })

  it("offers the archive first and selected, with JSON and PDF beside it", async () => {
    await openDialog()

    expect(screen.getByRole("button", { name: /^Archive/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
    expect(screen.getByRole("button", { name: /^JSON/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    )
    expect(screen.getByRole("button", { name: /^PDF/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    )
  })

  it("says in one sentence what the file will hold, and cites no regulation", async () => {
    const user = await openDialog()

    await user.click(screen.getByRole("button", { name: "Continue" }))
    const dialog = screen.getByRole("dialog")
    expect(dialog).toHaveTextContent(
      "The archive will include Maria Lopez's details, sessions and notes.",
    )
    expect(dialog.textContent).not.toMatch(/HIPAA|CFR|164\./)
  })

  it("asks the endpoint for the defaults and saves under the server's name", async () => {
    const blob = new Blob(["PK"], { type: "application/zip" })
    mockDownload.mockResolvedValue({ blob, filename: "patient_patient-1_export.zip" })
    const user = await openDialog()

    await user.click(screen.getByRole("button", { name: "Continue" }))
    await user.click(screen.getByRole("button", { name: "Download" }))

    expect(mockDownload).toHaveBeenCalledWith("patient-1", "zip", {
      includeTranscripts: false,
      includePsychotherapyNotes: false,
    })
    expect(mockSave).toHaveBeenCalledWith(blob, "patient_patient-1_export.zip")
    expect(await screen.findByText("Your download has started.")).toBeInTheDocument()
  })

  it("still sends JSON, and names it, when JSON is chosen", async () => {
    const blob = new Blob(["{}"], { type: "application/json" })
    mockDownload.mockResolvedValue({ blob, filename: "patient_patient-1_export.json" })
    const user = await openDialog()

    await user.click(screen.getByRole("button", { name: /^JSON/ }))
    await user.click(screen.getByRole("button", { name: "Continue" }))
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "The JSON file will include Maria Lopez's details, sessions and notes.",
    )
    await user.click(screen.getByRole("button", { name: "Download" }))

    expect(mockDownload).toHaveBeenCalledWith("patient-1", "json", {
      includeTranscripts: false,
      includePsychotherapyNotes: false,
    })
    expect(mockSave).toHaveBeenCalledWith(blob, "patient_patient-1_export.json")
  })

  it("reopens on the archive after another format was picked", async () => {
    const user = await openDialog()
    await user.click(screen.getByRole("button", { name: /^PDF/ }))
    await user.click(screen.getByRole("button", { name: "Cancel" }))

    await user.click(screen.getByRole("button", { name: "Export" }))

    expect(screen.getByRole("button", { name: /^Archive/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
  })

  it("passes the format and both options through when they are chosen", async () => {
    mockDownload.mockResolvedValue({ blob: new Blob(["%PDF"]), filename: "chart.pdf" })
    const user = await openDialog()

    await user.click(screen.getByRole("button", { name: /^PDF/ }))
    await user.click(
      screen.getByRole("checkbox", { name: "Include session transcripts" }),
    )
    await user.click(
      screen.getByRole("checkbox", { name: "Include psychotherapy notes" }),
    )
    await user.click(screen.getByRole("button", { name: "Continue" }))
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "The PDF will include Maria Lopez's details, sessions, notes and session transcripts, plus your psychotherapy notes.",
    )
    await user.click(screen.getByRole("button", { name: "Download" }))

    expect(mockDownload).toHaveBeenCalledWith("patient-1", "pdf", {
      includeTranscripts: true,
      includePsychotherapyNotes: true,
    })
    expect(mockSave).toHaveBeenCalledWith(expect.any(Blob), "chart.pdf")
  })

  it("stays on the confirm step and says so when the download fails", async () => {
    mockDownload.mockRejectedValue(new Error("network"))
    const user = await openDialog()

    await user.click(screen.getByRole("button", { name: "Continue" }))
    await user.click(screen.getByRole("button", { name: "Download" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The export didn't download. Try again.",
    )
    expect(mockSave).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Download" })).toBeInTheDocument()
  })
})

describe("exportSummary", () => {
  it("names only what is going in", () => {
    expect(exportSummary("json", "Sam Lee", true, false)).toBe(
      "The JSON file will include Sam Lee's details, sessions, notes and session transcripts.",
    )
    expect(exportSummary("pdf", "Sam Lee", false, true)).toBe(
      "The PDF will include Sam Lee's details, sessions and notes, plus your psychotherapy notes.",
    )
    expect(exportSummary("zip", "Sam Lee", false, false)).toBe(
      "The archive will include Sam Lee's details, sessions and notes.",
    )
  })
})
