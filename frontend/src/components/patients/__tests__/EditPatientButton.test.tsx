// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import type { PatientResponse } from "@/types/patients"

const { readOnlyMode } = vi.hoisted(() => ({ readOnlyMode: { readOnly: false } }))

vi.mock("@/lib/access/readOnlyMode", () => ({ useReadOnlyMode: () => readOnlyMode }))
// The form has its own suite; here it only has to report what it was asked to show.
vi.mock("../PatientForm", () => ({
  PatientForm: ({ mode, patient, open }: { mode: string; patient?: PatientResponse; open: boolean }) =>
    open ? <div role="dialog">{`${mode} ${patient?.id}`}</div> : null,
}))

import { EditPatientButton } from "../EditPatientButton"

const patient = { id: "patient-123", first_name: "Jane", last_name: "Doe" } as PatientResponse

describe("EditPatientButton", () => {
  beforeEach(() => {
    readOnlyMode.readOnly = false
  })

  it("opens the edit form for this patient", () => {
    render(<EditPatientButton patient={patient} />)
    expect(screen.queryByRole("dialog")).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Edit" }))

    expect(screen.getByRole("dialog").textContent).toBe("edit patient-123")
  })

  it("is hidden in read-only mode", () => {
    readOnlyMode.readOnly = true
    render(<EditPatientButton patient={patient} />)
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull()
  })
})
