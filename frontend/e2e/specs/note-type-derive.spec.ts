// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { randomBytes } from "node:crypto"

import { ApiError } from "../fixtures/api"
import { test, expect } from "../fixtures/auth"

// The stack's stand-in (NOTE_GENERATION_BASE_URL) answers a derive call with
// one fixed proposal — a follow-up note with Interval and Plan sections — and
// extracts a sample by moving each "Label: text" line into the field whose
// key is that label. A line with no such field lands nowhere, which is what
// the coverage check reports.

type Spec = {
  label: string
  sections: Array<{ key: string; label: string; fields: Array<{ key: string; kind: string; ai_hint: string }> }>
  inputs: unknown[]
}
type Derived = {
  spec: Spec
  coverage: Array<{ sample: number; passages: number; unplaced: string[]; checked: boolean }>
  guard: Array<{ path: string; outcome: string }>
  reference: { key: string; label: string } | null
  suggestions: Array<{ label: string; description: string }>
}
type NoteType = { key: string; version: number | null; sections: Array<{ key: string }> }
type NoteTypeList = { note_types: Array<{ key: string }> }

const STRAY = "The client brought a drawing from a weekend art class to show."
const SAMPLE = [
  "Interval history: Sleeping better since the last visit, appetite steady.",
  "Current medications: sertraline 50 mg daily",
  "Follow up: Return in four weeks for a medication check.",
  STRAY,
].join("\n")

function form(fields: Record<string, string | string[]>): FormData {
  const body = new FormData()
  for (const [name, value] of Object.entries(fields)) {
    for (const item of Array.isArray(value) ? value : [value]) body.append(name, item)
  }
  return body
}

test.describe("deriving a note type from a sample note", () => {
  test("a pasted sample yields a proposal, a coverage report, and saves as a type", async ({ api }) => {
    const before = await api.get<NoteTypeList>("/api/note-types")

    const derived = await api.postForm<Derived>("/api/note-types/derive", form({ samples: SAMPLE }))

    expect(derived.spec.sections.map((s) => s.key)).toEqual(["interval", "plan"])
    expect(derived.guard).toEqual([])
    expect(derived.coverage).toEqual([{ sample: 0, passages: 4, unplaced: [STRAY], checked: true }])
    // Proposing saves nothing.
    const after = await api.get<NoteTypeList>("/api/note-types")
    expect(after.note_types.map((t) => t.key)).toEqual(before.note_types.map((t) => t.key))

    const slug = `e2e_derived_${randomBytes(3).toString("hex")}`
    const saved = await api.put<NoteType>(`/api/note-types/custom/${slug}`, derived.spec)
    try {
      expect(saved.key).toBe(`custom.${slug}`)
      expect(saved.version).toBe(1)
      expect(saved.sections.map((s) => s.key)).toEqual(["interval", "plan"])
    } finally {
      await api.delete(`/api/note-types/custom/${slug}`)
    }
  })

  test("a file sample and a built-in reference: what the proposal lacks comes back", async ({ api }) => {
    const body = form({ reference: "soap" })
    body.append("files", new Blob([SAMPLE], { type: "text/plain" }), "note.txt")

    const derived = await api.postForm<Derived>("/api/note-types/derive", body)

    expect(derived.coverage[0].unplaced).toEqual([STRAY])
    expect(derived.reference).toEqual({ key: "soap", label: "SOAP" })
    expect(derived.suggestions.map((s) => s.label)).toEqual(["Subjective", "Objective", "Assessment"])
  })

  test("a description alone is enough", async ({ api }) => {
    const derived = await api.postForm<Derived>(
      "/api/note-types/derive",
      form({ description: "What changed since last time, then the plan." }),
    )

    expect(derived.spec.label).toBe("Follow-up visit")
    expect(derived.coverage).toEqual([])
  })

  test("a request with nothing to derive from is refused", async ({ api }) => {
    let error: unknown
    try {
      await api.postForm("/api/note-types/derive", form({ description: "   " }))
    } catch (caught) {
      error = caught
    }

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 400 })
  })
})
