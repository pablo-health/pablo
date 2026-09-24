// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Catalog-driven note rendering: every note type but SOAP and Narrative is
 * laid out from its definition, and edits save back as
 * ``{section: {field: value}}``.
 */

import { afterEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { NoteViewer } from "../NoteViewer"
import { getNoteType } from "@/lib/api/noteTypes"
import { ApiError } from "@/lib/api/client"
import { createMockNote } from "@/test/factories"
import { noteContentToJson, type NoteContent } from "@/types/sessions"
import type { NoteTypeSchema } from "@/types/noteTypes"

vi.mock("@/lib/api/noteTypes", () => ({
  getNoteType: vi.fn(),
  listNoteTypes: vi.fn(),
}))

vi.mock("@/lib/utils/pdfExport", () => ({ exportSOAPToPDF: vi.fn() }))

vi.mock("@/lib/config", () => ({
  useConfig: () => ({ showVerificationBadges: true }),
}))

const DAP: NoteTypeSchema = {
  key: "dap",
  label: "DAP",
  description: "Data / Assessment / Plan",
  tier: "extension",
  context: "session",
  inputs: [],
  version: null,
  sections: [
    {
      key: "data",
      label: "Data",
      fields: [
        { key: "subjective", label: "Client report", kind: "text", ai_hint: "" },
        { key: "observations", label: "Observations", kind: "list", ai_hint: "" },
      ],
    },
    {
      key: "assessment",
      label: "Assessment",
      fields: [{ key: "impression", label: "Clinical impression", kind: "text", ai_hint: "" }],
    },
    {
      key: "plan",
      label: "Plan",
      fields: [
        { key: "next_steps", label: "Next steps", kind: "list", ai_hint: "" },
        { key: "measures", label: "Measures", kind: "structured", ai_hint: "" },
      ],
    },
  ],
}

const DAP_CONTENT = {
  data: {
    subjective: "Reports sleeping better",
    observations: ["Calm", "Engaged"],
  },
  assessment: { impression: "Improving" },
  plan: { next_steps: ["Continue CBT"], measures: { phq9: 7 } },
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

afterEach(() => {
  vi.clearAllMocks()
})

describe("catalog-driven note view", () => {
  it("renders each section and field from the definition", async () => {
    vi.mocked(getNoteType).mockResolvedValue(DAP)
    render(
      <NoteViewer note={createMockNote({ note_type: "dap", content: DAP_CONTENT })} />,
      { wrapper },
    )

    expect(await screen.findByRole("heading", { name: "DAP" })).toBeInTheDocument()
    for (const heading of ["Data", "Assessment", "Plan"]) {
      expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument()
    }
    expect(screen.getByText("Reports sleeping better")).toBeInTheDocument()
    expect(screen.getByText("Calm")).toBeInTheDocument()
    expect(screen.getByText("Engaged")).toBeInTheDocument()
    expect(screen.getByText("Improving")).toBeInTheDocument()
    expect(screen.getByText(/"phq9": 7/)).toBeInTheDocument()
    expect(screen.getByText("AI Generated")).toBeInTheDocument()
    // Not a SOAP note in disguise.
    expect(screen.queryByText("Subjective")).not.toBeInTheDocument()
  })

  it("asks for the definition at the version the note records", async () => {
    vi.mocked(getNoteType).mockResolvedValue({ ...DAP, key: "custom.coach", version: 3 })
    render(
      <NoteViewer
        note={createMockNote({
          note_type: "custom.coach",
          note_type_version: 3,
          content: DAP_CONTENT,
        })}
      />,
      { wrapper },
    )
    await screen.findByRole("heading", { name: "DAP" })
    expect(getNoteType).toHaveBeenCalledWith("custom.coach", 3, undefined)
  })

  it("shows clinician edits over the generated content", async () => {
    vi.mocked(getNoteType).mockResolvedValue(DAP)
    render(
      <NoteViewer
        note={createMockNote({
          note_type: "dap",
          content: DAP_CONTENT,
          content_edited: { ...DAP_CONTENT, assessment: { impression: "Stable" } },
        })}
      />,
      { wrapper },
    )
    expect(await screen.findByText("Stable")).toBeInTheDocument()
    expect(screen.queryByText("Improving")).not.toBeInTheDocument()
    expect(screen.getByText("Edited")).toBeInTheDocument()
  })

  it("saves edits as {section: {field}}, splitting lists on newlines", async () => {
    vi.mocked(getNoteType).mockResolvedValue(DAP)
    const onSave = vi.fn<(content: NoteContent) => void>()
    render(
      <NoteViewer
        note={createMockNote({
          note_type: "dap",
          content: { ...DAP_CONTENT, extra: { kept: "yes" } },
        })}
        onSave={onSave}
      />,
      { wrapper },
    )

    fireEvent.click(await screen.findByRole("button", { name: /edit/i }))
    fireEvent.change(screen.getByLabelText("Clinical impression"), {
      target: { value: "Much improved" },
    })
    fireEvent.change(screen.getByLabelText("Observations"), {
      target: { value: "Calm\n\n  Smiling  \n" },
    })
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }))

    expect(onSave).toHaveBeenCalledTimes(1)
    expect(noteContentToJson(onSave.mock.calls[0][0])).toEqual({
      data: { subjective: "Reports sleeping better", observations: ["Calm", "Smiling"] },
      assessment: { impression: "Much improved" },
      plan: { next_steps: ["Continue CBT"], measures: { phq9: 7 } },
      extra: { kept: "yes" },
    })
  })

  it("opens a blank note straight in the editor", async () => {
    vi.mocked(getNoteType).mockResolvedValue(DAP)
    const onSave = vi.fn<(content: NoteContent) => void>()
    render(<NoteViewer note={createMockNote({ note_type: "dap" })} onSave={onSave} />, {
      wrapper,
    })

    fireEvent.change(await screen.findByLabelText("Client report"), {
      target: { value: "First session" },
    })
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }))

    expect(noteContentToJson(onSave.mock.calls[0][0])).toEqual({
      data: { subjective: "First session", observations: [] },
      assessment: { impression: "" },
      plan: { next_steps: [] },
    })
  })

  it("offers no editing when the note is read-only", async () => {
    vi.mocked(getNoteType).mockResolvedValue(DAP)
    render(
      <NoteViewer
        note={createMockNote({ note_type: "dap", content: DAP_CONTENT })}
        onSave={vi.fn()}
        readonly
      />,
      { wrapper },
    )
    await screen.findByRole("heading", { name: "DAP" })
    expect(screen.queryByRole("button", { name: /edit/i })).not.toBeInTheDocument()
  })

  it("shows a calm message when the type can't be found", async () => {
    vi.mocked(getNoteType).mockRejectedValue(
      new ApiError("NOT_FOUND", "Note type 'custom.gone' not found", undefined, 404),
    )
    render(
      <NoteViewer note={createMockNote({ note_type: "custom.gone", content: DAP_CONTENT })} />,
      { wrapper },
    )
    expect(
      await screen.findByText("This note's layout couldn't be loaded."),
    ).toBeInTheDocument()
    expect(screen.queryByText("SOAP Note")).not.toBeInTheDocument()
  })

  it("keeps SOAP and Narrative on their own views", () => {
    const { unmount } = render(
      <NoteViewer
        note={createMockNote({
          note_type: "soap",
          content: { subjective: "S", objective: "O", assessment: "A", plan: "P" },
        })}
      />,
      { wrapper },
    )
    expect(screen.getByText("SOAP Note")).toBeInTheDocument()
    unmount()

    render(
      <NoteViewer note={createMockNote({ note_type: "narrative", content: { body: "Hello" } })} />,
      { wrapper },
    )
    expect(screen.getByText("Narrative Note")).toBeInTheDocument()
    expect(screen.getByText("Hello")).toBeInTheDocument()
    expect(getNoteType).not.toHaveBeenCalled()
  })
})
