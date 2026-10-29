// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What a note shows: the clinician's edit over the original, for every
 * shape a SOAP note is stored in.
 */

import { describe, expect, it } from "vitest"
import { displayedNote } from "../displayedNote"
import { structuredToNarrative } from "@/components/sessions/SubFieldEditor"
import {
  createMockNote,
  createMockSOAPNote,
  createMockStructuredSOAPNote,
} from "@/test/factories"

// Generation stores the four structured sections and no narrative beside them.
function draftedContent(): Record<string, unknown> {
  const { narrative: _omitted, ...sections } = createMockStructuredSOAPNote()
  return sections as unknown as Record<string, unknown>
}

const EDIT = createMockSOAPNote({ plan: "Practice paced breathing" })

describe("displayedNote", () => {
  it("shows the clinician's edit over a drafted note, without the draft's sources", () => {
    const note = createMockNote({ content: draftedContent(), content_edited: { ...EDIT } })
    expect(displayedNote(note)).toEqual({
      content: { note_type: "soap", ...EDIT },
      edited: true,
      draft: null,
    })
  })

  it("prefers an unsaved edit over the saved one", () => {
    const note = createMockNote({ content: draftedContent(), content_edited: { ...EDIT } })
    const pending = { note_type: "soap" as const, ...createMockSOAPNote({ plan: "Newer" }) }
    expect(displayedNote(note, pending).content).toEqual(pending)
  })

  it("derives the narrative from a drafted note no one has edited", () => {
    const content = draftedContent()
    const shown = displayedNote(createMockNote({ content }))
    expect(shown.edited).toBe(false)
    expect(shown.draft).toEqual(content)
    expect(shown.content).toEqual({
      note_type: "soap",
      ...structuredToNarrative(createMockStructuredSOAPNote()),
    })
  })

  it("uses the stored narrative when the draft carries one", () => {
    const structured = createMockStructuredSOAPNote()
    const shown = displayedNote(
      createMockNote({ content: structured as unknown as Record<string, unknown> }),
    )
    expect(shown.content).toEqual({ note_type: "soap", ...structured.narrative })
    expect(shown.draft).toEqual(structured)
  })

  it("has nothing to show for a note with no content yet", () => {
    expect(displayedNote(createMockNote({ content: null }))).toEqual({
      content: null,
      edited: false,
      draft: null,
    })
  })

  it("shows a narrative note's edit over its original", () => {
    const note = createMockNote({
      note_type: "narrative",
      content: { body: "Original" },
      content_edited: { body: "Edited" },
    })
    expect(displayedNote(note).content).toEqual({ note_type: "narrative", body: "Edited" })
  })
})
