// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The notice on an imported note: where it came from, what it was there and
 * when, and who signed it there, in the document's own words.
 */

import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import { ImportedNoteNotice } from "../ImportedNoteNotice"
import type { NoteSource } from "@/types/notes"

const progressNote: NoteSource = {
  system: "simplepractice",
  kind: "progress",
  title: "Progress Note",
  signed_by: "Avery Provider",
  signed_at: "2026-09-27T10:44:00",
  created_at: "2026-09-27T10:44:00",
  visit_date: "2026-09-25T14:00:00",
  diagnoses: [],
}

describe("ImportedNoteNotice", () => {
  it("names the system, the note's visit and its signer for an archive import", () => {
    render(<ImportedNoteNotice sessionDate="2026-09-25T14:00:00" signer={null} source={progressNote} />)

    const notice = screen.getByRole("region", { name: "Imported note" })
    expect(notice).toHaveTextContent("Imported from SimplePractice")
    expect(notice).toHaveTextContent("Progress note for the visit on September 25, 2026.")
    expect(notice).toHaveTextContent("Signed there by Avery Provider on September 27, 2026")
  })

  it("dates a note with no visit by the day it was written there", () => {
    render(
      <ImportedNoteNotice
        sessionDate={null}
        signer={null}
        source={{
          ...progressNote,
          kind: "chart",
          title: "Chart Note",
          signed_by: null,
          signed_at: null,
          created_at: "2026-09-26T23:30:00",
          visit_date: null,
        }}
      />,
    )

    const notice = screen.getByRole("region", { name: "Imported note" })
    expect(notice).toHaveTextContent("Chart note, written there on September 26, 2026.")
    expect(notice).not.toHaveTextContent("Signed")
  })

  it("says only that a single-document import was documented elsewhere", () => {
    render(<ImportedNoteNotice sessionDate="2026-08-19T14:30:00" signer="A. Rivera, PMHNP-BC" />)

    const notice = screen.getByRole("region", { name: "Imported note" })
    expect(notice).toHaveTextContent("Imported")
    expect(notice).not.toHaveTextContent("Imported from")
    expect(notice).toHaveTextContent("Documented elsewhere for the visit on August 19, 2026.")
    expect(notice).toHaveTextContent("Signed by A. Rivera, PMHNP-BC")
  })
})
