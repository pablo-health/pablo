// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import {
  noteHeading,
  noteListDate,
  noteOrigin,
  noteStatus,
  sourceSystemName,
  withNoteSuffix,
} from "../noteDisplay"
import { createMockNote } from "@/test/factories"
import type { NoteSource } from "@/types/notes"

const fromSimplePractice: NoteSource = {
  system: "simplepractice",
  kind: "chart",
  title: "Chart Note",
  signed_by: null,
  signed_at: null,
  created_at: "2026-09-26T23:30:00",
  visit_date: null,
  diagnoses: [],
}

describe("noteStatus", () => {
  it("never calls an imported note a draft: it was signed where it was written", () => {
    expect(noteStatus(createMockNote({ finalized_at: null, imported: true })).label).toBe(
      "Imported",
    )
  })

  it("keeps draft and finalized for every other note", () => {
    expect(noteStatus(createMockNote({ finalized_at: null })).label).toBe("Draft")
    expect(noteStatus(createMockNote({ finalized_at: "2026-08-19T15:00:00Z" })).label).toBe(
      "Finalized",
    )
  })
})

describe("noteHeading", () => {
  it("calls an imported note what it was where it was written, in sentence case", () => {
    expect(noteHeading("Narrative", createMockNote({ source: fromSimplePractice }))).toBe(
      "Chart note",
    )
  })

  it("reads '<type> note' for a note written here, without doubling the word", () => {
    expect(noteHeading("SOAP", createMockNote())).toBe("SOAP note")
    expect(noteHeading("Psychotherapy note", createMockNote())).toBe("Psychotherapy note")
    expect(withNoteSuffix("Progress Note")).toBe("Progress Note")
  })
})

describe("noteOrigin", () => {
  it("names the system an archive import brought the note from", () => {
    expect(noteOrigin(createMockNote({ source: fromSimplePractice, imported: true }))).toBe(
      "Imported from SimplePractice",
    )
  })

  it("says Imported for a note read from a single document, with no system named", () => {
    expect(noteOrigin(createMockNote({ imported: true, session_id: "s1" }))).toBe("Imported")
  })

  it("says whether any other note hangs off a session", () => {
    expect(noteOrigin(createMockNote({ session_id: "s1" }))).toBe("Session")
    expect(noteOrigin(createMockNote({ session_id: null }))).toBe("Standalone")
  })
})

describe("noteListDate", () => {
  it("shows the visit an imported note records before when it was finalized", () => {
    const note = createMockNote({
      finalized_at: "2026-09-26T23:30:00",
      source: { ...fromSimplePractice, kind: "progress", visit_date: "2026-09-25T14:00:00" },
    })
    expect(noteListDate(note)).toBe("2026-09-25T14:00:00")
  })

  it("falls back to finalized, then last changed", () => {
    expect(noteListDate(createMockNote({ finalized_at: "2026-08-19T15:00:00Z" }))).toBe(
      "2026-08-19T15:00:00Z",
    )
    const draft = createMockNote({ finalized_at: null })
    expect(noteListDate(draft)).toBe(draft.updated_at)
  })
})

describe("sourceSystemName", () => {
  it("spells a known system the way its users do, and leaves an unknown one as given", () => {
    expect(sourceSystemName("simplepractice")).toBe("SimplePractice")
    expect(sourceSystemName("somethingelse")).toBe("somethingelse")
    expect(sourceSystemName(null)).toBeNull()
  })
})
