// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * NewNoteButton Component Tests
 *
 * Covers the two on-ramps: launching the transcript upload (patient
 * pre-filled) and creating a blank note from a session note type.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"

import { NewNoteButton } from "../NewNoteButton"
import type { NoteTypeSchema } from "@/types/noteTypes"

const mockMutateAsync = vi.fn()
const mockPush = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}))

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

let mockCatalog: { note_types: NoteTypeSchema[] } | undefined

vi.mock("@/hooks/useNoteTypes", () => ({
  useNoteTypes: () => ({ data: mockCatalog, isLoading: false }),
}))

vi.mock("@/hooks/useNotes", () => ({
  useCreateStandaloneNote: () => ({
    mutateAsync: mockMutateAsync,
    isPending: false,
  }),
}))

// Capture what NewNoteButton passes to the transcript dialog so we can assert
// the handoff without exercising the full upload form.
let transcriptProps: { patientId?: string; open?: boolean } = {}

vi.mock("@/components/sessions/UploadTranscriptDialog", () => ({
  UploadTranscriptDialog: (props: { patientId?: string; open?: boolean }) => {
    transcriptProps = props
    return (
      <div data-testid="transcript-dialog" data-open={String(props.open)}>
        transcript dialog ({props.patientId})
      </div>
    )
  },
}))

// Stubbed for the same reason as the transcript dialog: it pulls in
// react-query (useImportNotes), which this picker-focused test doesn't wire.
vi.mock("@/components/sessions/ImportNotesDialog", () => ({
  ImportNotesDialog: (props: { patientId?: string; open?: boolean }) => (
    <div data-testid="import-dialog" data-open={String(props.open)}>
      import dialog ({props.patientId})
    </div>
  ),
}))

function soapType(): NoteTypeSchema {
  return {
    key: "soap",
    label: "SOAP",
    description: "Subjective, Objective, Assessment, Plan",
    context: "session",
    is_locked: false,
  } as NoteTypeSchema
}

describe("NewNoteButton", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCatalog = { note_types: [soapType()] }
    transcriptProps = {}
  })

  it("opens the transcript dialog (patient pre-filled) from the menu", () => {
    render(<NewNoteButton patientId="patient-1" />)

    fireEvent.click(screen.getByRole("button", { name: /new note/i }))
    fireEvent.click(screen.getByText("From a transcript"))

    expect(transcriptProps.patientId).toBe("patient-1")
    expect(transcriptProps.open).toBe(true)
  })

  it("creates a blank note and routes to it", async () => {
    mockMutateAsync.mockResolvedValue({ id: "note-9" })
    render(<NewNoteButton patientId="patient-1" />)

    fireEvent.click(screen.getByRole("button", { name: /new note/i }))
    fireEvent.click(screen.getByText("SOAP"))

    expect(mockMutateAsync).toHaveBeenCalledWith({
      patientId: "patient-1",
      data: { note_type: "soap" },
    })
    await vi.waitFor(() =>
      expect(mockPush).toHaveBeenCalledWith(
        "/dashboard/patients/patient-1/notes/note-9",
      ),
    )
  })

  describe("read-only deployment mode", () => {
    afterEach(() => {
      vi.unstubAllEnvs()
    })

    it("renders nothing, closing off the transcript-upload and import flows, when read-only", () => {
      vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")
      const { container } = render(<NewNoteButton patientId="patient-1" />)

      expect(
        screen.queryByRole("button", { name: /new note/i }),
      ).not.toBeInTheDocument()
      expect(screen.queryByTestId("transcript-dialog")).not.toBeInTheDocument()
      expect(screen.queryByTestId("import-dialog")).not.toBeInTheDocument()
      expect(container).toBeEmptyDOMElement()
    })

    it("shows the New note button when the flag is unset", () => {
      render(<NewNoteButton patientId="patient-1" />)

      expect(
        screen.getByRole("button", { name: /new note/i }),
      ).toBeInTheDocument()
    })
  })
})
