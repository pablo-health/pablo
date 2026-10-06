// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Session page note saving, against the real page component.
 *
 * Every edit, SOAP or any other type, is saved to the note's own edits
 * endpoint as soon as it is made, so a reload, a redraft and signing all
 * read it from the note rather than from this page.
 */

import { Suspense } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, fireEvent, render, screen } from "@testing-library/react"
import SessionDetailPage from "../page"
import { createMockNote, createMockSession } from "@/test/factories"
import type { Note } from "@/types/notes"
import type { NoteContent } from "@/types/sessions"

const { mockUseSession, mockSaveEdits, mockRedraft, mockAddDictation, finalizeProps } =
  vi.hoisted(() => ({
    mockUseSession: vi.fn(),
    mockSaveEdits: vi.fn(),
    mockRedraft: vi.fn(),
    mockAddDictation: vi.fn(),
    finalizeProps: { current: null as Record<string, unknown> | null },
  }))

vi.mock("@/hooks/useDictations", () => ({
  useSessionDictations: () => ({ data: { data: [] } }),
  useAddSessionDictation: () => ({ mutateAsync: mockAddDictation }),
}))

// Stand-in recorder: one button that sends a fixed clip.
vi.mock("@/components/sessions/DictateMore", () => ({
  DictateMore: ({
    onSend,
  }: {
    onSend: (clip: { blob: Blob; seconds: number }) => Promise<unknown>
  }) => <button onClick={() => onSend({ blob: new Blob(["v"]), seconds: 9 })}>dictate</button>,
}))

vi.mock("@/hooks/useSessions", () => ({
  useSession: () => mockUseSession(),
  useUpdateSessionMetadata: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateSessionRating: () => ({ mutateAsync: vi.fn() }),
  useRedraftSessionNote: () => ({ mutate: mockRedraft, isPending: false, isError: false }),
}))

vi.mock("@/hooks/useNotes", () => ({
  useUpdateNoteEdits: () => ({
    mutateAsync: mockSaveEdits,
    isError: false,
    isPending: false,
  }),
}))

vi.mock("@/hooks/useNoteTypes", () => ({
  useNoteTypeLabel: () => (key: string) =>
    ({ soap: "SOAP", dap: "DAP" } as Record<string, string>)[key] ?? key,
}))

// Reads the practice setting and the client's consent record; not under test here.
vi.mock("@/components/sessions/NoteConsentLine", () => ({ NoteConsentLine: () => null }))
vi.mock("@/components/sessions/VisitTimesPanel", () => ({ VisitTimesPanel: () => null }))

// Stand-in details panel: one button that redrafts with a fixed value.
vi.mock("@/components/sessions/NoteInputsPanel", () => ({
  NoteInputsPanel: ({
    onRedraft,
    hasEdits,
  }: {
    onRedraft: (data: unknown) => void
    hasEdits: boolean
  }) => (
    <button
      onClick={() =>
        onRedraft({ note_inputs: { visit_code: "99214" }, ...(hasEdits ? { edits: "keep" } : {}) })
      }
    >
      redraft
    </button>
  ),
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
  FinalizeButton: (props: Record<string, unknown>) => {
    finalizeProps.current = props
    return <button>Finalize</button>
  },
}))
vi.mock("@/components/payments/ChargeCardSection", () => ({
  ChargeCardSection: () => <div />,
}))
vi.mock("@/components/notes/signing/NoteSignaturePanel", () => ({
  NoteSignaturePanel: () => null,
}))
vi.mock("@/hooks/useNoteSigning", () => ({ useNoteSigning: () => ({ data: undefined }) }))
vi.mock("@/hooks/usePreferences", () => ({ useUserTimeZone: () => "UTC" }))

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

function givenSession(
  note: Note | null,
  status: "pending_review" | "processing" | "finalized" = "pending_review",
) {
  mockUseSession.mockReturnValue({
    data: createMockSession({ status, note }),
    isLoading: false,
    error: null,
  })
}

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

beforeEach(() => {
  mockSaveEdits.mockResolvedValue({})
})

afterEach(() => {
  vi.clearAllMocks()
  finalizeProps.current = null
})

describe("session page note save", () => {
  it("saves a non-SOAP edit to the note straight away", async () => {
    givenSession(createMockNote({ id: "note-9", note_type: "dap", content: {} }))
    await renderPage()

    expect(await screen.findByRole("heading", { name: "DAP note" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "save edit" }))

    expect(mockSaveEdits).toHaveBeenCalledTimes(1)
    expect(mockSaveEdits.mock.calls[0][0]).toEqual({
      noteId: "note-9",
      data: {
        content_edited: { data: { subjective: "Edited report", observations: ["Calm"] } },
      },
    })
    // The saved edit is shown back as the pending edit, still typed as the
    // note's own type rather than coerced to SOAP.
    expect(JSON.parse(screen.getByTestId("pending").textContent ?? "null")).toEqual(DAP_EDIT)
  })

  it("saves a SOAP edit under review to the note straight away, not at signing", async () => {
    givenSession(createMockNote({ id: "note-2", note_type: "soap", content: {} }))
    await renderPage()

    expect(await screen.findByRole("heading", { name: "SOAP note" })).toBeInTheDocument()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "save edit" }))
    })

    expect(mockSaveEdits).toHaveBeenCalledWith({
      noteId: "note-2",
      data: { content_edited: { subjective: "S", objective: "O", assessment: "A", plan: "P" } },
    })
    expect(JSON.parse(screen.getByTestId("pending").textContent ?? "null")).toEqual(SOAP_EDIT)
    // Signing locks what the note holds; it carries no body of its own.
    expect(finalizeProps.current).not.toBeNull()
    expect(finalizeProps.current).not.toHaveProperty("soapNoteEdited")
  })

  it("stops showing an edit the note didn't take", async () => {
    mockSaveEdits.mockRejectedValue(new Error("nope"))
    givenSession(createMockNote({ note_type: "soap", content: {} }))
    await renderPage()

    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: "save edit" }))
    })

    expect(JSON.parse(screen.getByTestId("pending").textContent ?? "null")).toBeNull()
  })

  it("saves a SOAP edit straight to the note once an unlocked note has no finalize left", async () => {
    givenSession(
      createMockNote({ id: "note-3", note_type: "soap", content: {}, finalized_at: null }),
      "finalized",
    )
    await renderPage()

    expect(await screen.findByRole("heading", { name: "SOAP note" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "save edit" }))

    expect(mockSaveEdits).toHaveBeenCalledTimes(1)
    expect(mockSaveEdits.mock.calls[0][0].noteId).toBe("note-3")
  })

  it("offers no save on a signed note", async () => {
    givenSession(
      createMockNote({ note_type: "soap", content: {}, finalized_at: "2026-10-01T12:00:00Z" }),
      "finalized",
    )
    await renderPage()

    expect(await screen.findByRole("heading", { name: "SOAP note" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "save edit" }))
    expect(mockSaveEdits).not.toHaveBeenCalled()
  })

  it("starts a redraft that keeps edits only once the edit is saved", async () => {
    const save = deferred()
    mockSaveEdits.mockReturnValue(save.promise)
    givenSession(createMockNote({ id: "note-4", note_type: "soap", content: {} }))
    await renderPage()

    fireEvent.click(await screen.findByRole("button", { name: "save edit" }))
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "redraft" }))
    })
    expect(mockRedraft).not.toHaveBeenCalled()

    await act(async () => save.resolve())
    expect(mockRedraft).toHaveBeenCalledWith({
      sessionId: "session-123",
      data: { note_inputs: { visit_code: "99214" }, edits: "keep" },
    })
    // The redraft's result is what shows next, not the page's copy of the edit.
    expect(JSON.parse(screen.getByTestId("pending").textContent ?? "null")).toBeNull()
  })

  it("sends a dictation that redrafts the note only once the edit is saved", async () => {
    const save = deferred()
    mockSaveEdits.mockReturnValue(save.promise)
    givenSession(createMockNote({ id: "note-5", note_type: "soap", content: { s: 1 } }))
    await renderPage()

    fireEvent.click(await screen.findByRole("button", { name: "save edit" }))
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "dictate" }))
    })
    expect(mockAddDictation).not.toHaveBeenCalled()

    await act(async () => save.resolve())
    expect(mockAddDictation).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: "session-123", durationSeconds: 9 }),
    )
  })

  it("says a redraft is running, and holds the note still meanwhile", async () => {
    givenSession(createMockNote({ note_type: "dap", content: {}, status: "processing" }))
    await renderPage()

    expect(await screen.findByRole("status")).toHaveTextContent("Redrafting the note…")
    fireEvent.click(screen.getByRole("button", { name: "save edit" }))
    expect(mockSaveEdits).not.toHaveBeenCalled()
  })

  it("says a redraft that didn't finish left the note as it was", async () => {
    givenSession(createMockNote({ note_type: "dap", content: {}, status: "failed" }))
    await renderPage()

    expect(await screen.findByRole("alert")).toHaveTextContent("your note hasn't changed")
  })

  it("names no note type while the note is still being generated", async () => {
    givenSession(null, "processing")
    await renderPage()

    expect(await screen.findByText("Note is being generated…")).toBeInTheDocument()
    expect(screen.queryByText(/SOAP/)).not.toBeInTheDocument()
  })
})
