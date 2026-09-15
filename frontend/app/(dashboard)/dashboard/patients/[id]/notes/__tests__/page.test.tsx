// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Tests for the patient notes list page.
 *
 * Note: mirrors the sessions detail page tests — logic is exercised through
 * a small wrapper that takes `patientId` as a prop instead of the async
 * `params` promise, which needs a full Next.js environment to resolve.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen } from "@testing-library/react"
import * as usePatients from "@/hooks/usePatients"
import * as useNotesHooks from "@/hooks/useNotes"
import { renderWithProviders } from "@/test/renderWithProviders"
import { createMockPatient, createMockNote } from "@/test/factories"
import { noteHref, noteStatus, formatNoteDateTime } from "@/lib/noteDisplay"

function TestPatientNotesListPage({ patientId }: { patientId: string }) {
  const { data: patient, isLoading: patientLoading } = usePatients.usePatient(patientId)
  const { data: notesData, isLoading: notesLoading, error } = useNotesHooks.usePatientNotes(patientId)

  if (patientLoading) {
    return <div data-testid="loading">Loading...</div>
  }

  if (!patient) {
    return <div data-testid="patient-not-found">Patient not found.</div>
  }

  if (notesLoading) {
    return <div data-testid="notes-loading">Loading notes...</div>
  }

  if (error) {
    return (
      <div data-testid="notes-error">
        {error instanceof Error ? error.message : "Failed to load notes."}
      </div>
    )
  }

  if (!notesData || notesData.total === 0) {
    return <div data-testid="notes-empty">No notes yet for this patient.</div>
  }

  return (
    <table data-testid="notes-table">
      <tbody>
        {notesData.data.map((note) => {
          const status = noteStatus(note)
          return (
            <tr key={note.id}>
              <td>
                <a href={noteHref(patient.id, note)}>{note.note_type}</a>
              </td>
              <td>{note.session_id ? "Session" : "Standalone"}</td>
              <td>{formatNoteDateTime(note.finalized_at ?? note.updated_at)}</td>
              <td>{status.label}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

describe("PatientNotesListPage Integration", () => {
  const patient = createMockPatient({
    id: "patient-a",
    first_name: "Alice",
    last_name: "Anders",
  })

  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(usePatients, "usePatient").mockReturnValue({
      data: patient,
      isLoading: false,
      error: null,
    } as any)
  })

  it("shows loading state while fetching the patient", () => {
    vi.spyOn(usePatients, "usePatient").mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as any)
    vi.spyOn(useNotesHooks, "usePatientNotes").mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as any)

    renderWithProviders(<TestPatientNotesListPage patientId="patient-a" />)

    expect(screen.getByTestId("loading")).toBeInTheDocument()
  })

  it("shows patient-not-found when the patient does not exist", () => {
    vi.spyOn(usePatients, "usePatient").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
    } as any)
    vi.spyOn(useNotesHooks, "usePatientNotes").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
    } as any)

    renderWithProviders(<TestPatientNotesListPage patientId="patient-a" />)

    expect(screen.getByTestId("patient-not-found")).toBeInTheDocument()
  })

  it("shows an empty state when the patient has no notes", () => {
    vi.spyOn(useNotesHooks, "usePatientNotes").mockReturnValue({
      data: { data: [], total: 0 },
      isLoading: false,
      error: null,
    } as any)

    renderWithProviders(<TestPatientNotesListPage patientId="patient-a" />)

    expect(screen.getByTestId("notes-empty")).toBeInTheDocument()
  })

  it("shows an error state when notes fail to load", () => {
    vi.spyOn(useNotesHooks, "usePatientNotes").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("Network error"),
    } as any)

    renderWithProviders(<TestPatientNotesListPage patientId="patient-a" />)

    expect(screen.getByTestId("notes-error")).toHaveTextContent("Network error")
  })

  it("renders notes with type, source, and status", () => {
    const sessionNote = createMockNote({
      id: "note-1",
      note_type: "soap",
      session_id: "session-1",
      finalized_at: "2024-01-15T14:30:00Z",
    })
    const standaloneNote = createMockNote({
      id: "note-2",
      note_type: "narrative",
      session_id: null,
      finalized_at: null,
    })
    vi.spyOn(useNotesHooks, "usePatientNotes").mockReturnValue({
      data: { data: [sessionNote, standaloneNote], total: 2 },
      isLoading: false,
      error: null,
    } as any)

    renderWithProviders(<TestPatientNotesListPage patientId="patient-a" />)

    const rows = screen.getAllByRole("row")
    expect(rows).toHaveLength(2)
    expect(screen.getByText("Session")).toBeInTheDocument()
    expect(screen.getByText("Standalone")).toBeInTheDocument()
    expect(screen.getByText("Finalized")).toBeInTheDocument()
    expect(screen.getByText("Draft")).toBeInTheDocument()
    expect(screen.getByText("soap").closest("a")).toHaveAttribute(
      "href",
      "/dashboard/sessions/session-1",
    )
    expect(screen.getByText("narrative").closest("a")).toHaveAttribute(
      "href",
      "/dashboard/patients/patient-a/notes/note-2",
    )
  })
})
