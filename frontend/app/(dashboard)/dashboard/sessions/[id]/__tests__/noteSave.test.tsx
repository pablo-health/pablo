// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Session page note saving, against the real page component.
 *
 * SOAP edits are held locally and ride on finalize; every other note type
 * has no slot there, so its edits must reach the note's own edits endpoint.
 */

import { Suspense } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { act, fireEvent, render, screen } from "@testing-library/react"
import SessionDetailPage from "../page"
import { createMockNote, createMockSession } from "@/test/factories"
import type { Note } from "@/types/notes"
import type { NoteContent } from "@/types/sessions"

const { mockUseSession, mockUpdateEdits, finalizeProps } = vi.hoisted(() => ({
  mockUseSession: vi.fn(),
  mockUpdateEdits: vi.fn(),
  finalizeProps: { soapNoteEdited: undefined as unknown },
}))

vi.mock("@/hooks/useSessions", () => ({
  useSession: () => mockUseSession(),
  useUpdateSessionMetadata: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateSessionRating: () => ({ mutateAsync: vi.fn() }),
}))

vi.mock("@/hooks/useNotes", () => ({
  useUpdateNoteEdits: () => ({ mutate: mockUpdateEdits, isError: false }),
}))

vi.mock("@/hooks/useNoteTypes", () => ({
  useNoteTypeLabel: () => (key: string) =>
    ({ soap: "SOAP", dap: "DAP" } as Record<string, string>)[key] ?? key,
}))

const DAP_EDIT: NoteContent = {
  note_type: "schema",
  key: "dap",
  sections: { data: { subjective: "Edited report", observations: ["Calm"] } },
}

const SOAP_EDIT: NoteContent = {
  note_type: "soap",
  subjective: "S",
  objective: "O",
  assessment: "A",
  plan: "P",
}

// Stand-in viewer: one button that saves a fixed edit for the note's type,
// and a readout of what the page hands back as the pending edit.
vi.mock("@/components/sessions/NoteViewer", () => ({
  NoteViewer: ({
    note,
    pendingEdited,
    onSave,
  }: {
    note: Note
    pendingEdited: NoteContent | null
    onSave?: (c: NoteContent) => void
  }) => (
    <div>
      <button onClick={() => onSave?.(note.note_type === "soap" ? SOAP_EDIT : DAP_EDIT)}>
        save edit
      </button>
      <pre data-testid="pending">{JSON.stringify(pendingEdited)}</pre>
    </div>
  ),
}))

vi.mock("@/components/sessions/SessionDetailHeader", () => ({
  SessionDetailHeader: () => <div />,
}))
vi.mock("@/components/sessions/TranscriptViewer", () => ({
  TranscriptViewer: () => <div />,
}))
vi.mock("@/components/sessions/QualityRating", () => ({ QualityRating: () => <div /> }))
vi.mock("@/components/sessions/QualityRatingWithFeedback", () => ({
  QualityRatingWithFeedback: () => <div />,
}))
vi.mock("@/components/sessions/FinalizeButton", () => ({
  FinalizeButton: ({ soapNoteEdited }: { soapNoteEdited: unknown }) => {
    finalizeProps.soapNoteEdited = soapNoteEdited
    return <button>Finalize</button>
  },
}))
vi.mock("@/components/payments/ChargeCardSection", () => ({
  ChargeCardSection: () => <div />,
}))

// The page reads its params with use(), which suspends; the awaited act lets
// that promise settle before the assertions run.
async function renderPage() {
  await act(async () => {
    render(
      <Suspense fallback={null}>
        <SessionDetailPage params={Promise.resolve({ id: "session-123" })} />
      </Suspense>,
    )
  })
}

function givenSession(note: Note | null, status: "pending_review" | "processing" = "pending_review") {
  mockUseSession.mockReturnValue({
    data: createMockSession({ status, note }),
    isLoading: false,
    error: null,
  })
}

afterEach(() => {
  vi.clearAllMocks()
  finalizeProps.soapNoteEdited = undefined
})

describe("session page note save", () => {
  it("saves a non-SOAP edit to the note straight away", async () => {
    givenSession(createMockNote({ id: "note-9", note_type: "dap", content: {} }))
    await renderPage()

    expect(await screen.findByRole("heading", { name: "DAP note" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "save edit" }))

    expect(mockUpdateEdits).toHaveBeenCalledTimes(1)
    expect(mockUpdateEdits.mock.calls[0][0]).toEqual({
      noteId: "note-9",
      data: {
        content_edited: { data: { subjective: "Edited report", observations: ["Calm"] } },
      },
    })
    // The saved edit is shown back as the pending edit, still typed as the
    // note's own type rather than coerced to SOAP.
    expect(JSON.parse(screen.getByTestId("pending").textContent ?? "null")).toEqual(DAP_EDIT)
    expect(finalizeProps.soapNoteEdited).toBeNull()
  })

  it("keeps SOAP edits local for finalize to persist", async () => {
    givenSession(createMockNote({ note_type: "soap", content: {} }))
    await renderPage()

    expect(await screen.findByRole("heading", { name: "SOAP note" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "save edit" }))

    expect(mockUpdateEdits).not.toHaveBeenCalled()
    expect(finalizeProps.soapNoteEdited).toEqual({
      subjective: "S",
      objective: "O",
      assessment: "A",
      plan: "P",
    })
  })

  it("names no note type while the note is still being generated", async () => {
    givenSession(null, "processing")
    await renderPage()

    expect(await screen.findByText("Note is being generated…")).toBeInTheDocument()
    expect(screen.queryByText(/SOAP/)).not.toBeInTheDocument()
  })
})
