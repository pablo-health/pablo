// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { randomBytes } from "node:crypto"

import { ApiError } from "../fixtures/api"
import { test, expect } from "../fixtures/auth"

// The stack drafts through its stand-in (NOTE_GENERATION_BASE_URL), which
// answers every field with "Stand-in draft for <section>.<field>." — so a
// spec can check a draft has the type's own shape without a model.

type Draft = { key: string; version: number | null; sections: Record<string, Record<string, unknown>> }
type NoteTypeList = { note_types: Array<{ key: string }> }

const TRANSCRIPT = {
  format: "txt",
  content: "[00:00:05] Therapist: How have things been?\n[00:00:09] Client: Steady, thanks.",
}

const FOLLOW_UP = {
  label: "Follow-up (e2e)",
  description: "A two-section follow-up used to check previews.",
  sections: [
    {
      key: "subjective",
      label: "Subjective",
      fields: [
        { key: "interval_history", label: "Interval history", ai_hint: "What changed since last visit." },
        { key: "current_medications", label: "Current medications", kind: "list" },
      ],
    },
    {
      key: "plan",
      label: "Plan",
      fields: [{ key: "follow_up", label: "Follow-up", ai_hint: "Return interval as stated." }],
    },
  ],
  inputs: [
    { key: "place_of_service", label: "Place of service", kind: "choice", options: ["Telehealth", "In office"], required: true },
  ],
}

const EXPECTED_SECTIONS = {
  subjective: {
    interval_history: "Stand-in draft for subjective.interval_history.",
    current_medications: ["Stand-in draft for subjective.current_medications."],
  },
  plan: { follow_up: "Stand-in draft for plan.follow_up." },
}

test.describe("previewing a note type's draft", () => {
  test("an unsaved definition drafts in its own shape and is not stored", async ({ api }) => {
    const draft = await api.post<Draft>("/api/note-types/preview", {
      spec: FOLLOW_UP,
      transcript: TRANSCRIPT,
      inputs: { place_of_service: "Telehealth" },
    })

    expect(draft.key).toBe("custom.preview")
    expect(draft.version).toBeNull()
    expect(draft.sections).toEqual(EXPECTED_SECTIONS)
    const listed = await api.get<NoteTypeList>("/api/note-types")
    expect(listed.note_types.map((t) => t.key)).not.toContain("custom.preview")
  })

  test("a saved type drafts at its version", async ({ api }) => {
    const slug = `e2e_preview_${randomBytes(3).toString("hex")}`
    await api.request("PUT", `/api/note-types/custom/${slug}`, FOLLOW_UP)
    try {
      const draft = await api.post<Draft>("/api/note-types/preview", {
        key: `custom.${slug}`,
        transcript: TRANSCRIPT,
        inputs: { place_of_service: "In office" },
      })

      expect(draft.key).toBe(`custom.${slug}`)
      expect(draft.version).toBe(1)
      expect(draft.sections).toEqual(EXPECTED_SECTIONS)
    } finally {
      await api.request("DELETE", `/api/note-types/custom/${slug}`)
    }
  })

  test("a built-in type drafts too", async ({ api }) => {
    const draft = await api.post<Draft>("/api/note-types/preview", { key: "narrative", transcript: TRANSCRIPT })

    expect(draft.sections).toEqual({ note: { body: "Stand-in draft for note.body." } })
  })

  test("a missing required input is refused before anything is drafted", async ({ api }) => {
    let error: unknown
    try {
      await api.post("/api/note-types/preview", { spec: FOLLOW_UP, transcript: TRANSCRIPT })
    } catch (caught) {
      error = caught
    }

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 400 })
    expect((error as ApiError).body).toContain("INVALID_NOTE_INPUTS")
  })
})
