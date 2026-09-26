// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PatientExport: the format step offers transcripts and psychotherapy notes
 * as two opt-in choices, both off by default, matching the export endpoint.
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { PatientExport } from "@/components/patients/PatientExport"

vi.mock("@/hooks/usePatients", () => ({
  usePatient: () => ({ data: { first_name: "Maria", last_name: "Lopez" } }),
}))

vi.mock("@/hooks/useSessions", () => ({
  useSessionList: () => ({ data: { data: [] } }),
}))

async function openDialog() {
  const user = userEvent.setup()
  render(<PatientExport patientId="patient-1" patientName="Maria Lopez" />)
  await user.click(screen.getByRole("button", { name: /export patient data/i }))
  return user
}

describe("PatientExport options", () => {
  it("shows both choices unchecked on the format step", async () => {
    await openDialog()

    const transcripts = screen.getByRole("checkbox", {
      name: "Include session transcripts",
    })
    const psychotherapy = screen.getByRole("checkbox", {
      name: "Include psychotherapy notes",
    })
    expect(transcripts).not.toBeChecked()
    expect(psychotherapy).not.toBeChecked()
  })

  it("lists only what was chosen on the confirm step", async () => {
    const user = await openDialog()

    await user.click(screen.getByRole("button", { name: "Continue" }))
    expect(screen.queryByText("Session transcripts")).not.toBeInTheDocument()
    expect(screen.queryByText("Your psychotherapy notes")).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Back" }))
    await user.click(
      screen.getByRole("checkbox", { name: "Include session transcripts" }),
    )
    await user.click(
      screen.getByRole("checkbox", { name: "Include psychotherapy notes" }),
    )
    await user.click(screen.getByRole("button", { name: "Continue" }))

    expect(screen.getByText("Session transcripts")).toBeInTheDocument()
    expect(screen.getByText("Your psychotherapy notes")).toBeInTheDocument()
  })
})
