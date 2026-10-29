// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import type { NoteTypeBase } from "@/types/noteTypes"
import { basedDraftFrom, basedOnLine, canHideSection, shapeOf, specFromBasedDraft } from "../basedModel"
import { blankField, blankInput, blankSection } from "../editorModel"

const BASE: NoteTypeBase = {
  key: "follow_up",
  label: "Follow-up",
  description: "A follow-up.",
  slug: "follow_up",
  spec: {
    label: "Follow-up",
    description: "A follow-up.",
    system_prompt: "Base prompt.",
    user_template: "{fields}\n\n{transcript}",
    sections: [
      {
        key: "subjective",
        label: "Subjective",
        fields: [
          { key: "notes", label: "Notes", kind: "text", ai_hint: "" },
          { key: "sleep", label: "Sleep", kind: "text", ai_hint: "" },
        ],
      },
      { key: "risk", label: "Risk", fields: [{ key: "ideation", label: "Ideation", kind: "text", ai_hint: "" }] },
    ],
    inputs: [{ key: "place", label: "Place", kind: "text", options: [], required: false }],
  },
  required_fields: ["risk.ideation"],
  samples: [],
}

describe("based note-type model", () => {
  it("keys added parts so they never collide with the base's", () => {
    const draft = basedDraftFrom(BASE)
    draft.addedFields.subjective = [{ ...blankField(), label: "Notes", after: null }]
    draft.addedSections = [{ ...blankSection(), label: "Risk", fields: [{ ...blankField(), label: "Plan" }] }]
    draft.addedInputs = [{ ...blankInput(), label: "Place" }]

    const patch = specFromBasedDraft(draft).patch!

    expect(patch.add_fields.map((a) => a.field.key)).toEqual(["notes_2"])
    expect(patch.add_sections.map((a) => a.section.key)).toEqual(["risk_2"])
    expect(patch.add_inputs.map((i) => i.key)).toEqual(["place_2"])
  })

  it("reloads a saved patch and saves it back unchanged", () => {
    const spec = specFromBasedDraft({
      ...basedDraftFrom(BASE),
      hiddenFields: ["subjective.sleep"],
      instructions: "Be brief.",
    })
    spec.patch!.add_fields = [
      { section: "subjective", field: { key: "mood", label: "Mood", kind: "list", ai_hint: "" }, after: "notes" },
    ]
    spec.patch!.override = [{ path: "subjective", label: "History" }]

    expect(specFromBasedDraft(basedDraftFrom(BASE, { spec }))).toEqual(spec)
  })

  it("lays a draft out the way the server resolves it", () => {
    const spec = specFromBasedDraft(basedDraftFrom(BASE))
    spec.patch!.hide_fields = ["subjective.sleep"]
    spec.patch!.add_fields = [
      { section: "subjective", field: { key: "mood", label: "Mood", kind: "text", ai_hint: "" }, after: "notes" },
    ]
    spec.patch!.add_sections = [
      { section: { key: "plan", label: "Plan", fields: [{ key: "next", label: "Next", kind: "text", ai_hint: "" }] }, after: "subjective" },
    ]

    const shape = shapeOf(spec, BASE)

    expect(shape.sections.map((s) => `${s.key}:${s.fields.map((f) => f.key).join(",")}`)).toEqual([
      "subjective:notes,mood",
      "plan:next",
      "risk:ideation",
    ])
  })

  it("never lets a section holding a required field be hidden", () => {
    expect(canHideSection(BASE, BASE.spec.sections[0])).toBe(true)
    expect(canHideSection(BASE, BASE.spec.sections[1])).toBe(false)
  })

  it("names the base and counts the changes", () => {
    expect(basedOnLine({ key: "k", label: "Follow-up", additions: 1, hidden: 0 })).toBe(
      "Based on Follow-up; 1 addition, 0 hidden",
    )
  })
})
