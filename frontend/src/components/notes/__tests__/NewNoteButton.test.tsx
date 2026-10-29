// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * NewNoteButton Component Tests
 *
 * Covers the two on-ramps: launching the transcript upload (patient
 * pre-filled) and creating a blank note from a session note type.
 */

import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { NewNoteButton } from "../NewNoteButton"
import { ApiError } from "@/lib/api/client"
import type { NoteTypeSchema } from "@/types/noteTypes"

// Radix Select needs pointer capture, which jsdom lacks.
beforeAll(() => {
  Element.prototype.hasPointerCapture = vi.fn()
  Element.prototype.releasePointerCapture = vi.fn()
})

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

function followUpType(): NoteTypeSchema {
  return {
    key: "psychiatric_follow_up",
    label: "Psychiatric follow-up",
    description: "E/M visit with psychotherapy",
    context: "session",
    is_locked: false,
    inputs: [
      {
        key: "place_of_service",
        label: "Place of service",
        kind: "choice",
        options: ["Telehealth", "In office"],
        required: true,
      },
      { key: "mdm_risk", label: "Risk", kind: "choice", options: ["Low"], required: false },
    ],
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

  it("says who can see a restricted type, and nothing for the rest", () => {
    mockCatalog = {
      note_types: [
        soapType(),
        {
          key: "psychotherapy",
          label: "Psychotherapy note",
          description: "Your own notes on a session.",
          context: "session",
          restricted: true,
          is_locked: false,
        } as NoteTypeSchema,
      ],
    }
    render(<NewNoteButton patientId="patient-1" />)

    fireEvent.click(screen.getByRole("button", { name: /new note/i }))

    expect(screen.getByText("Psychotherapy note")).toBeInTheDocument()
    expect(screen.getAllByText("Only you can see this note.")).toHaveLength(1)
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

  it("asks for a type's inputs before creating it, and sends them", async () => {
    // Catches a blank psychiatric note being posted with no place of
    // service, which the server rejects every time.
    const user = userEvent.setup()
    mockCatalog = { note_types: [soapType(), followUpType()] }
    mockMutateAsync.mockResolvedValue({ id: "note-9" })
    render(<NewNoteButton patientId="patient-1" />)

    await user.click(screen.getByRole("button", { name: /new note/i }))
    await user.click(screen.getByText("Psychiatric follow-up"))

    expect(mockMutateAsync).not.toHaveBeenCalled()
    // The MDM choices are made at review, beside the note.
    expect(screen.queryByRole("combobox", { name: "Risk" })).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Create" }))
    expect(await screen.findByText("Place of service is required")).toBeInTheDocument()
    expect(mockMutateAsync).not.toHaveBeenCalled()

    await user.click(screen.getByRole("combobox", { name: "Place of service" }))
    await user.click(await screen.findByRole("option", { name: "In office" }))
    await user.click(screen.getByRole("button", { name: "Create" }))

    expect(mockMutateAsync).toHaveBeenCalledWith({
      patientId: "patient-1",
      data: {
        note_type: "psychiatric_follow_up",
        note_inputs: { place_of_service: "In office" },
      },
    })
    await vi.waitFor(() =>
      expect(mockPush).toHaveBeenCalledWith(
        "/dashboard/patients/patient-1/notes/note-9",
      ),
    )
  })

  it("goes back to the list of note types", async () => {
    const user = userEvent.setup()
    mockCatalog = { note_types: [soapType(), followUpType()] }
    render(<NewNoteButton patientId="patient-1" />)

    await user.click(screen.getByRole("button", { name: /new note/i }))
    await user.click(screen.getByText("Psychiatric follow-up"))
    await user.click(screen.getByRole("button", { name: "Back" }))

    expect(screen.getByText("From a transcript")).toBeInTheDocument()
    expect(
      screen.queryByRole("combobox", { name: "Place of service" }),
    ).not.toBeInTheDocument()
  })

  it("says so beside the inputs when the server rejects them", async () => {
    // Catches a rejected value surfacing only as a toast behind the dialog.
    const user = userEvent.setup()
    mockCatalog = { note_types: [followUpType()] }
    mockMutateAsync.mockRejectedValue(
      new ApiError(
        "INVALID_NOTE_INPUTS",
        "'In office' is not an option for 'place_of_service'",
        {},
        400,
      ),
    )
    render(<NewNoteButton patientId="patient-1" />)

    await user.click(screen.getByRole("button", { name: /new note/i }))
    await user.click(screen.getByText("Psychiatric follow-up"))
    await user.click(screen.getByRole("combobox", { name: "Place of service" }))
    await user.click(await screen.findByRole("option", { name: "In office" }))
    await user.click(screen.getByRole("button", { name: "Create" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "These note details weren't accepted",
    )
    expect(mockPush).not.toHaveBeenCalled()
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
