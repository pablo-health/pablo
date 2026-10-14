// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ImportNotesDialog: the note type a batch is read into.
 *
 * The picker offers the visit-note types "New note" starts from, less the
 * hand-written and locked ones, defaults to SOAP, and hands the chosen key to
 * the import run.
 */

import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { ImportNotesDialog } from "../ImportNotesDialog"
import type { NoteTypeSchema } from "@/types/noteTypes"

beforeAll(() => {
  // Radix Select asks for pointer capture, which jsdom lacks.
  Element.prototype.hasPointerCapture = vi.fn()
  Element.prototype.releasePointerCapture = vi.fn()
  Element.prototype.scrollIntoView = vi.fn()
})

const push = vi.fn()
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }))

const start = vi.fn()
const IDLE = { items: [] as unknown[], isRunning: false, isComplete: false, doneCount: 0 }
let run = IDLE
vi.mock("@/hooks/useImportNotes", () => ({
  useImportNotes: () => ({ ...run, errorCount: 0, start, reset: vi.fn() }),
}))

function noteType(key: string, label: string, extra: Partial<NoteTypeSchema> = {}): NoteTypeSchema {
  return { key, label, description: "", context: "session", ...extra } as NoteTypeSchema
}

const CATALOG = {
  note_types: [
    noteType("soap", "SOAP"),
    noteType("custom.psychiatric_follow_up", "Psychiatric follow-up"),
    noteType("psychotherapy", "Psychotherapy note", { restricted: true }),
    noteType("dap", "DAP", { is_locked: true }),
    noteType("treatment_plan", "Treatment plan", { context: "patient" }),
  ],
}

vi.mock("@/hooks/useNoteTypes", () => ({
  useNoteTypes: () => ({ data: CATALOG, isLoading: false }),
}))

function chooseFile() {
  const input = screen.getByLabelText("Choose note files to import")
  return userEvent.upload(input, new File(["Chief complaint: sleep"], "prior.txt", { type: "text/plain" }))
}

describe("ImportNotesDialog", () => {
  beforeEach(() => {
    start.mockReset()
    push.mockReset()
    run = IDLE
  })

  it("offers the visit-note types a model can read a note into", async () => {
    const user = userEvent.setup()
    render(<ImportNotesDialog patientId="p1" open onOpenChange={vi.fn()} />)

    await user.click(screen.getByRole("combobox", { name: "Import as" }))

    const options = screen.getAllByRole("option").map((o) => o.textContent)
    expect(options).toEqual(["SOAP", "Psychiatric follow-up"])
  })

  it("imports as SOAP unless another type is picked", async () => {
    const user = userEvent.setup()
    render(<ImportNotesDialog patientId="p1" open onOpenChange={vi.fn()} />)

    expect(screen.getByRole("combobox", { name: "Import as" })).toHaveTextContent("SOAP")
    await chooseFile()
    await user.click(screen.getByRole("button", { name: "Import 1 note" }))

    expect(start).toHaveBeenCalledWith([expect.any(File)], "soap")
  })

  it("hands the picked type to the import", async () => {
    const user = userEvent.setup()
    render(<ImportNotesDialog patientId="p1" open onOpenChange={vi.fn()} />)

    await user.click(screen.getByRole("combobox", { name: "Import as" }))
    await user.click(screen.getByRole("option", { name: "Psychiatric follow-up" }))
    await chooseFile()
    await user.click(screen.getByRole("button", { name: "Import 1 note" }))

    expect(start).toHaveBeenCalledWith([expect.any(File)], "custom.psychiatric_follow_up")
  })

  it("opens an imported note itself, never the Review list it is not on", async () => {
    const user = userEvent.setup()
    const file = new File(["Chief complaint: sleep"], "prior.txt", { type: "text/plain" })
    run = {
      items: [{ id: "i1", file, status: "done", session: { id: "s1", session_date: null } }],
      isRunning: false,
      isComplete: true,
      doneCount: 1,
    }
    render(<ImportNotesDialog patientId="p1" open onOpenChange={vi.fn()} />)

    expect(screen.queryByRole("button", { name: /Review/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Open note" }))

    expect(push).toHaveBeenCalledWith("/dashboard/sessions/s1")
  })
})
