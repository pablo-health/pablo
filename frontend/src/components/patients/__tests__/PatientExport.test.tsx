// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PatientExport: the archive is the default, JSON and PDF stay a click away,
 * and the choices made are the ones sent.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { PatientExport, exportSummary } from "../PatientExport"

const mockDownloadPatientExport = vi.fn()
const mockSaveFile = vi.fn()

vi.mock("@/lib/api/patients", () => ({
  downloadPatientExport: (...args: unknown[]) => mockDownloadPatientExport(...args),
}))
vi.mock("@/lib/saveFile", () => ({
  saveFile: (...args: unknown[]) => mockSaveFile(...args),
}))

const formatButton = (name: RegExp) => screen.getByRole("button", { name })

async function openDialog() {
  const user = userEvent.setup()
  render(<PatientExport patientId="p1" patientName="Robin Ash" />)
  await user.click(screen.getByRole("button", { name: "Export" }))
  return user
}

describe("PatientExport", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockDownloadPatientExport.mockResolvedValue({
      blob: new Blob(["PK"]),
      filename: "patient_p1_export_2026-09-26.zip",
    })
  })

  it("offers the archive first and selected, with JSON and PDF beside it", async () => {
    await openDialog()

    expect(formatButton(/^Archive/)).toHaveAttribute("aria-pressed", "true")
    expect(formatButton(/^JSON/)).toHaveAttribute("aria-pressed", "false")
    expect(formatButton(/^PDF/)).toHaveAttribute("aria-pressed", "false")
  })

  it("downloads the archive with the defaults when nothing is changed", async () => {
    const user = await openDialog()

    await user.click(screen.getByRole("button", { name: "Continue" }))
    expect(
      screen.getByText("The archive will include Robin Ash's details, sessions and notes."),
    ).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Download" }))

    expect(mockDownloadPatientExport).toHaveBeenCalledWith("p1", "zip", {
      includeTranscripts: false,
      includePsychotherapyNotes: false,
    })
    expect(mockSaveFile).toHaveBeenCalledWith(
      expect.any(Blob),
      "patient_p1_export_2026-09-26.zip",
    )
    expect(await screen.findByText("Your download has started.")).toBeInTheDocument()
  })

  it("still sends JSON when JSON is chosen", async () => {
    const user = await openDialog()

    await user.click(formatButton(/^JSON/))
    await user.click(screen.getByRole("button", { name: "Continue" }))
    await user.click(screen.getByRole("button", { name: "Download" }))

    expect(mockDownloadPatientExport).toHaveBeenCalledWith("p1", "json", expect.any(Object))
  })

  it("reopens on the archive after another format was used", async () => {
    const user = await openDialog()
    await user.click(formatButton(/^PDF/))
    await user.click(screen.getByRole("button", { name: "Cancel" }))

    await user.click(screen.getByRole("button", { name: "Export" }))

    expect(formatButton(/^Archive/)).toHaveAttribute("aria-pressed", "true")
  })
})

describe("exportSummary", () => {
  it("names the archive and only what goes in", () => {
    expect(exportSummary("zip", "Robin Ash", true, true)).toBe(
      "The archive will include Robin Ash's details, sessions, notes and session transcripts, plus your psychotherapy notes.",
    )
  })
})
