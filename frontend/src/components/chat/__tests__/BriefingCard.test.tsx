// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * BriefingCard (§13.4) — sage-tinted Fraunces-italic empty state that
 * composes a lay-language sentence describing what Pablo will read.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"

import { BriefingCard } from "../BriefingCard"
import type { SourceSelection } from "@/lib/chat/types"
import type { Note } from "@/types/notes"

vi.mock("@/hooks/usePatients", () => ({
  usePatient: vi.fn(),
}))
vi.mock("@/hooks/useNotes", () => ({
  usePatientNotes: vi.fn(),
}))

import { usePatient } from "@/hooks/usePatients"
import { usePatientNotes } from "@/hooks/useNotes"

const mockUsePatient = usePatient as unknown as ReturnType<typeof vi.fn>
const mockUsePatientNotes = usePatientNotes as unknown as ReturnType<typeof vi.fn>

type NoteSeed = {
  id: string
  note_type: string
  created_at: string
}

function note(seed: NoteSeed): Note {
  // Cast through ``unknown`` because the FE Note type narrows note_type
  // to OSS-known values ("soap" | "narrative"), but the bundler — and
  // therefore the briefing card — operates on the broader open-string
  // set ("intake", "treatment_plan", ...).
  return {
    id: seed.id,
    patient_id: "patient-1",
    session_id: null,
    note_type: seed.note_type as unknown as Note["note_type"],
    content: null,
    content_edited: null,
    finalized_at: null,
    quality_rating: null,
    quality_rating_reason: null,
    quality_rating_sections: null,
    export_status: "not_queued",
    export_queued_at: null,
    export_reviewed_at: null,
    export_reviewed_by: null,
    exported_at: null,
    created_at: seed.created_at,
    updated_at: seed.created_at,
  }
}

function setHooks(firstName: string | null, notes: Note[]) {
  mockUsePatient.mockReturnValue({
    data: firstName ? { first_name: firstName } : undefined,
    isLoading: !firstName,
  })
  mockUsePatientNotes.mockReturnValue({
    data: { data: notes, total: notes.length },
    isLoading: false,
  })
}

const FULL_SELECTION: SourceSelection = {
  most_recent_intake: true,
  treatment_plan_active: true,
  safety_plan_active: true,
  progress_notes_recent: { limit: 5 },
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("BriefingCard", () => {
  it("composes the full pattern when intake, treatment plan, and progress notes are present", () => {
    setHooks("Maria", [
      note({
        id: "intake-1",
        note_type: "intake",
        created_at: "2026-03-03T10:00:00Z",
      }),
      note({
        id: "tp-1",
        note_type: "treatment_plan",
        created_at: "2026-04-01T10:00:00Z",
      }),
      note({
        id: "sp-1",
        note_type: "safety_plan",
        created_at: "2026-04-15T10:00:00Z",
      }),
      note({
        id: "soap-1",
        note_type: "soap",
        created_at: "2026-05-09T10:00:00Z",
      }),
      note({
        id: "soap-2",
        note_type: "soap",
        created_at: "2026-05-02T10:00:00Z",
      }),
      note({
        id: "soap-3",
        note_type: "narrative",
        created_at: "2026-04-25T10:00:00Z",
      }),
    ])

    render(<BriefingCard patientId="patient-1" selection={FULL_SELECTION} />)
    const sentence = screen.getByText(/^I'm reading Maria's/i)
    // Head phrase keeps no leading "the" so the possessive reads
    // cleanly ("Maria's most recent intake …").
    expect(sentence.textContent).toMatch(
      /Maria's most recent intake from March 3/,
    )
    expect(sentence.textContent).toMatch(/the active treatment plan/)
    expect(sentence.textContent).toMatch(/the active safety plan/)
    expect(sentence.textContent).toMatch(
      /3 most recent progress notes \(last from May 9\)/,
    )
    // Oxford-and join
    expect(sentence.textContent).toMatch(/, and 3 most recent progress notes/)
  })

  it("omits a source whose backing notes are missing (row_count 0)", () => {
    // No safety_plan notes — should not be mentioned.
    setHooks("Maria", [
      note({
        id: "intake-1",
        note_type: "intake",
        created_at: "2026-03-03T10:00:00Z",
      }),
      note({
        id: "tp-1",
        note_type: "treatment_plan",
        created_at: "2026-04-01T10:00:00Z",
      }),
    ])

    render(
      <BriefingCard
        patientId="patient-1"
        selection={{
          most_recent_intake: true,
          treatment_plan_active: true,
          safety_plan_active: true,
        }}
      />,
    )
    const sentence = screen.getByText(/^I'm reading Maria's/i)
    expect(sentence.textContent).toMatch(
      /Maria's most recent intake from March 3/,
    )
    expect(sentence.textContent).toMatch(/the active treatment plan/)
    expect(sentence.textContent).not.toMatch(/safety plan/i)
  })

  it("clamps progress-notes count by available notes when fewer exist than the limit", () => {
    setHooks("Sam", [
      note({
        id: "soap-1",
        note_type: "soap",
        created_at: "2026-05-09T10:00:00Z",
      }),
    ])
    render(
      <BriefingCard
        patientId="patient-1"
        selection={{ progress_notes_recent: { limit: 10 } }}
      />,
    )
    // Singular "note" not plural — only 1 available
    expect(
      screen.getByText(/1 most recent progress note \(last from May 9\)/),
    ).toBeInTheDocument()
  })

  it("falls back to a neutral invitation when no sources resolve to content", () => {
    setHooks("Alex", [])
    render(
      <BriefingCard
        patientId="patient-1"
        selection={{ most_recent_intake: true }}
      />,
    )
    expect(
      screen.getByText(/I'm ready to chat about Alex\./i),
    ).toBeInTheDocument()
  })

  it("uses a generic stand-in when the patient's first name hasn't loaded yet", () => {
    setHooks(null, [])
    render(
      <BriefingCard
        patientId="patient-1"
        selection={{ progress_notes_recent: true }}
      />,
    )
    expect(
      screen.getByText(/I'm ready to chat about this patient\./i),
    ).toBeInTheDocument()
  })

  it("always renders the 'Ask me anything.' invitation line", () => {
    setHooks("Maria", [])
    render(
      <BriefingCard patientId="patient-1" selection={{}} />,
    )
    expect(screen.getByText("Ask me anything.")).toBeInTheDocument()
  })

  it("does not mention pasted_text — the user already knows what they pasted in", () => {
    setHooks("Maria", [])
    render(
      <BriefingCard
        patientId="patient-1"
        selection={{ pasted_text: { content: "long paste" } }}
      />,
    )
    // Falls through to the neutral invitation since no chart sources
    // resolve to content for this minimal selection.
    expect(
      screen.getByText(/I'm ready to chat about Maria\./i),
    ).toBeInTheDocument()
    expect(screen.queryByText(/pasted/i)).toBeNull()
  })

  it("uses the sage-tinted card surface (data-slot hook + secondary palette)", () => {
    setHooks("Maria", [])
    const { container } = render(
      <BriefingCard patientId="patient-1" selection={{}} />,
    )
    const card = container.querySelector("[data-slot='chat-briefing-card']")
    expect(card).not.toBeNull()
    expect(card?.className).toMatch(/secondary-50/)
    const sentence = container.querySelector(
      "[data-slot='chat-briefing-sentence']",
    )
    expect(sentence?.className).toMatch(/font-display/)
    expect(sentence?.className).toMatch(/italic/)
  })
})
