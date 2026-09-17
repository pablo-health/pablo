// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Tests for the standalone note detail page.
 *
 * Note: mirrors the sessions detail page tests — logic is exercised through
 * a small wrapper that takes `noteId`/`patientId` as props instead of the
 * async `params` promise, which needs a full Next.js environment to resolve.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen } from "@testing-library/react"
import * as usePatients from "@/hooks/usePatients"
import * as useNotesHooks from "@/hooks/useNotes"
import { renderWithProviders } from "@/test/renderWithProviders"
import { createMockPatient, createMockNote } from "@/test/factories"

function TestStandaloneNotePage({
  patientId,
  noteId,
}: {
  patientId: string
  noteId: string
}) {
  const { data: patient } = usePatients.usePatient(patientId)
  const { data: note, isLoading, error } = useNotesHooks.useNote(noteId)

  if (isLoading) {
    return <div data-testid="loading">Loading...</div>
  }

  if (error || !note) {
    return <div data-testid="not-found">Note not found</div>
  }

  const patientName = patient ? `${patient.first_name} ${patient.last_name}` : "Patient"
  const isGenerating = note.status === "processing"
  const generationFailed = note.status === "failed"

  return (
    <div data-testid="note-detail">
      <div data-testid="patient-name">{patientName}</div>
      {isGenerating ? (
        <div data-testid="generating">Note is being generated…</div>
      ) : generationFailed ? (
        <div data-testid="generation-failed">Note generation failed.</div>
      ) : (
        <div data-testid="note-viewer">{note.note_type}</div>
      )}
    </div>
  )
}

describe("StandaloneNotePage Integration", () => {
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

  it("shows loading state while fetching the note", () => {
    vi.spyOn(useNotesHooks, "useNote").mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as any)

    renderWithProviders(<TestStandaloneNotePage patientId="patient-a" noteId="note-1" />)

    expect(screen.getByTestId("loading")).toBeInTheDocument()
  })

  it("shows a 404 state when the note fetch errors", () => {
    vi.spyOn(useNotesHooks, "useNote").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("Not found"),
    } as any)

    renderWithProviders(<TestStandaloneNotePage patientId="patient-a" noteId="missing" />)

    expect(screen.getByTestId("not-found")).toBeInTheDocument()
  })

  it("shows a 404 state when the note is null", () => {
    vi.spyOn(useNotesHooks, "useNote").mockReturnValue({
      data: null,
      isLoading: false,
      error: null,
    } as any)

    renderWithProviders(<TestStandaloneNotePage patientId="patient-a" noteId="missing" />)

    expect(screen.getByTestId("not-found")).toBeInTheDocument()
  })

  it("renders the note for the correct patient", () => {
    const note = createMockNote({
      id: "note-1",
      patient_id: "patient-a",
      note_type: "narrative",
      status: "complete",
    })
    vi.spyOn(useNotesHooks, "useNote").mockReturnValue({
      data: note,
      isLoading: false,
      error: null,
    } as any)

    renderWithProviders(<TestStandaloneNotePage patientId="patient-a" noteId="note-1" />)

    expect(screen.getByTestId("note-detail")).toBeInTheDocument()
    expect(screen.getByTestId("patient-name")).toHaveTextContent("Alice Anders")
    expect(screen.getByTestId("note-viewer")).toHaveTextContent("narrative")
  })

  it("shows a generating state while the note is processing", () => {
    const note = createMockNote({ id: "note-1", status: "processing" })
    vi.spyOn(useNotesHooks, "useNote").mockReturnValue({
      data: note,
      isLoading: false,
      error: null,
    } as any)

    renderWithProviders(<TestStandaloneNotePage patientId="patient-a" noteId="note-1" />)

    expect(screen.getByTestId("generating")).toBeInTheDocument()
    expect(screen.queryByTestId("note-viewer")).not.toBeInTheDocument()
  })
})
