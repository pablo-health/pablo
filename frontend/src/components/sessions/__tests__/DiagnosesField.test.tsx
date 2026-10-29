// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A `diagnoses` field: each diagnosis as the clinician stated it, code and
 * status kept apart, edited one row each.
 */

import { describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, within } from "@testing-library/react"
import { SchemaNoteBody } from "../SchemaNoteView"
import { noteContentToJson, type NoteContent } from "@/types/sessions"
import type { NoteTypeSchema } from "@/types/noteTypes"
import { statedDiagnoses } from "@/lib/statedDiagnoses"

const EVALUATION: NoteTypeSchema = {
  key: "custom.evaluation",
  label: "Evaluation",
  description: "",
  tier: "core",
  context: "session",
  inputs: [],
  version: 1,
  sections: [
    {
      key: "assessment",
      label: "Assessment",
      fields: [
        { key: "diagnoses", label: "Diagnoses", kind: "diagnoses", ai_hint: "" },
        { key: "rationale", label: "Rationale", kind: "text", ai_hint: "" },
      ],
    },
  ],
}

const STATED = [
  { label: "Generalized anxiety disorder", code: "F41.1", status: null },
  { label: "ADHD, inattentive", code: "F90.0", status: "rule out" },
]

function note(diagnoses: unknown) {
  return {
    note_type: "schema" as const,
    key: EVALUATION.key,
    sections: { assessment: { diagnoses, rationale: "As discussed." } },
  }
}

describe("diagnoses field", () => {
  it("shows each diagnosis with its code and status, and offers the action beside each", () => {
    const action = vi.fn((dx: { label: string }) => <button type="button">Act on {dx.label}</button>)
    render(<SchemaNoteBody definition={EVALUATION} note={note(STATED)} noteEdited={null} diagnosisAction={action} />)

    const items = screen.getAllByRole("listitem")
    expect(items.map((li) => li.textContent)).toEqual([
      "-Generalized anxiety disorder (F41.1)Act on Generalized anxiety disorder",
      "-ADHD, inattentive (F90.0), rule outAct on ADHD, inattentive",
    ])
    expect(action).toHaveBeenCalledWith(STATED[0])
    expect(action).toHaveBeenCalledWith(STATED[1])
  })

  it("shows a diagnosis written as a plain line, without guessing a code", () => {
    render(<SchemaNoteBody definition={EVALUATION} note={note(["Insomnia G47.00"])} noteEdited={null} />)

    expect(screen.getByRole("listitem")).toHaveTextContent("Insomnia G47.00")
    expect(statedDiagnoses(["Insomnia G47.00"])).toEqual([{ label: "Insomnia G47.00", code: null, status: null }])
  })

  it("edits one row per diagnosis and saves only rows that name one", () => {
    const onSave = vi.fn<(content: NoteContent) => void>()
    render(<SchemaNoteBody definition={EVALUATION} note={note(STATED)} noteEdited={null} onSave={onSave} />)

    fireEvent.click(screen.getByRole("button", { name: /edit/i }))
    const rows = screen.getByRole("group", { name: "Diagnoses" })
    fireEvent.change(within(rows).getByLabelText("Status 1"), { target: { value: "working" } })
    fireEvent.click(within(rows).getByRole("button", { name: "Remove diagnosis 2" }))
    fireEvent.click(within(rows).getByRole("button", { name: "Add a diagnosis" }))
    fireEvent.change(within(rows).getByLabelText("Diagnosis 2"), { target: { value: "  Insomnia " } })
    fireEvent.click(within(rows).getByRole("button", { name: "Add a diagnosis" }))
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }))

    expect(noteContentToJson(onSave.mock.calls[0][0])).toEqual({
      assessment: {
        diagnoses: [
          { label: "Generalized anxiety disorder", code: "F41.1", status: "working" },
          { label: "Insomnia", code: null, status: null },
        ],
        rationale: "As discussed.",
      },
    })
  })

  it("offers nothing beside a diagnosis while the note is being edited", () => {
    const action = vi.fn(() => <button type="button">Act</button>)
    render(
      <SchemaNoteBody
        definition={EVALUATION}
        note={note(STATED)}
        noteEdited={null}
        onSave={vi.fn()}
        diagnosisAction={action}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: /edit/i }))
    expect(screen.queryByRole("button", { name: "Act" })).not.toBeInTheDocument()
  })
})
