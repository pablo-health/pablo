// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The note's details on a session: shown with their values, changeable and
 * redrafted while the note is unsigned, read-only once it is signed, and
 * never redrafted over the clinician's edits without them choosing what
 * happens to the edits.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { NoteInputsPanel } from "../NoteInputsPanel"
import { createMockNote } from "@/test/factories"
import type { Note } from "@/types/notes"
import type { NoteInputSchema } from "@/types/noteTypes"

const INPUTS: NoteInputSchema[] = [
  { key: "visit_code", label: "Visit code", kind: "choice", options: ["99213", "99214"], required: true },
  { key: "program", label: "Program", kind: "text", options: [], required: false },
  // Set in the medical decision making panel, never here.
  { key: "mdm_problems", label: "Problems addressed", kind: "choice", options: ["low", "moderate"], required: false },
]

vi.mock("@/hooks/useNoteTypes", () => ({
  useNoteType: () => ({ data: { key: "custom.visit", inputs: INPUTS } }),
}))

function note(overrides: Partial<Note> = {}): Note {
  return createMockNote({
    note_type: "custom.visit",
    note_inputs: { visit_code: "99213" },
    ...overrides,
  })
}

function renderPanel(props: Partial<Parameters<typeof NoteInputsPanel>[0]> = {}) {
  const onRedraft = vi.fn()
  render(
    <NoteInputsPanel note={note()} editable hasEdits={false} onRedraft={onRedraft} {...props} />,
  )
  return onRedraft
}

describe("NoteInputsPanel", () => {
  it("shows the note's details with their current values", () => {
    renderPanel()
    expect(screen.getByLabelText(/Visit code/)).toHaveValue("99213")
    expect(screen.getByLabelText(/Program/)).toHaveValue("")
    expect(screen.getByRole("button", { name: "Save and redraft" })).toBeDisabled()
  })

  it("redrafts with a changed value", async () => {
    const onRedraft = renderPanel()
    await userEvent.selectOptions(screen.getByLabelText(/Visit code/), "99214")
    await userEvent.type(screen.getByLabelText(/Program/), "Weekly check-in")
    await userEvent.click(screen.getByRole("button", { name: "Save and redraft" }))

    expect(onRedraft).toHaveBeenCalledWith({
      note_inputs: { visit_code: "99214", program: "Weekly check-in" },
    })
  })

  it("won't redraft without a required value", async () => {
    renderPanel()
    await userEvent.selectOptions(screen.getByLabelText(/Visit code/), "")
    expect(screen.getByRole("button", { name: "Save and redraft" })).toBeDisabled()
  })

  it("asks what happens to edits, and keeps them by default", async () => {
    const onRedraft = renderPanel({ hasEdits: true })
    await userEvent.selectOptions(screen.getByLabelText(/Visit code/), "99214")
    await userEvent.click(screen.getByRole("button", { name: "Save and redraft" }))

    expect(onRedraft).not.toHaveBeenCalled()
    expect(screen.getByRole("radio", { name: /Keep my edits/ })).toBeChecked()
    await userEvent.click(screen.getByRole("button", { name: "Redraft" }))

    expect(onRedraft).toHaveBeenCalledWith({ note_inputs: { visit_code: "99214" }, edits: "keep" })
  })

  it("replaces edits only when that is chosen", async () => {
    const onRedraft = renderPanel({ hasEdits: true })
    await userEvent.selectOptions(screen.getByLabelText(/Visit code/), "99214")
    await userEvent.click(screen.getByRole("button", { name: "Save and redraft" }))
    await userEvent.click(screen.getByRole("radio", { name: /Redraft everything/ }))
    await userEvent.click(screen.getByRole("button", { name: "Redraft" }))

    expect(onRedraft).toHaveBeenCalledWith({
      note_inputs: { visit_code: "99214" },
      edits: "replace",
    })
  })

  it("is read-only on a signed note", () => {
    renderPanel({ editable: false, note: note({ finalized_at: "2026-10-05T19:04:00Z" }) })
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Save and redraft" })).not.toBeInTheDocument()
    expect(screen.getByText("99213")).toBeInTheDocument()
    expect(screen.getByText("Not provided")).toBeInTheDocument()
  })

  it("leaves the medical decision making choices to their own panel", () => {
    renderPanel({ note: note({ note_inputs: { visit_code: "99213", mdm_problems: "moderate" } }) })
    expect(screen.queryByLabelText("Problems addressed")).not.toBeInTheDocument()
  })

  it("holds still while a redraft runs", () => {
    renderPanel({ note: note({ status: "processing" }) })
    expect(screen.getByLabelText(/Visit code/)).toBeDisabled()
    expect(screen.getByRole("button", { name: "Save and redraft" })).toBeDisabled()
  })
})
