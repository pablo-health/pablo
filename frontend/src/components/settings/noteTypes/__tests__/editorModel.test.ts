// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { ApiError } from "@/lib/api/client"
import type { PracticeNoteTypeSpec } from "@/types/noteTypes"
import {
  blankDraft,
  blankField,
  draftFromSpec,
  fieldErrorsFrom,
  keyFromLabel,
  slugFor,
  specFromDraft,
} from "../editorModel"
import { importSpec } from "../importSpec"

const SPEC: PracticeNoteTypeSpec = {
  label: "Check-in",
  description: "",
  system_prompt: "Prompt.",
  user_template: null,
  sections: [{ key: "notes", label: "Notes", fields: [{ key: "body", label: "Body", kind: "text", ai_hint: "" }] }],
  inputs: [{ key: "where", label: "Where", kind: "choice", options: ["Office", "Video"], required: true }],
}

describe("note-type editor model", () => {
  it("round-trips a loaded spec unchanged", () => {
    expect(specFromDraft(draftFromSpec(SPEC, "check_in"))).toEqual(SPEC)
  })

  it("keeps the source of a field printed from the chart", () => {
    const charted: PracticeNoteTypeSpec = {
      ...SPEC,
      sections: [
        {
          key: "medications",
          label: "Medications and allergies",
          fields: [
            { key: "allergies", label: "Allergies", kind: "text", ai_hint: "", source: "allergies" },
            { key: "notes", label: "Notes", kind: "text", ai_hint: "Anything else." },
          ],
        },
      ],
    }
    const draft = draftFromSpec(charted, null)
    draft.sections[0].fields[0].label = "Allergies on file"

    expect(specFromDraft(draft).sections[0].fields).toEqual([
      { key: "allergies", label: "Allergies on file", kind: "text", ai_hint: "", source: "allergies" },
      { key: "notes", label: "Notes", kind: "text", ai_hint: "Anything else." },
    ])
    expect(importSpec(JSON.stringify(charted))).toEqual({ spec: charted })
  })

  it("derives keys for added parts from their names, unique among siblings", () => {
    const draft = draftFromSpec(SPEC, "check_in")
    draft.sections[0].fields.push({ ...blankField(), label: "Body" }, { ...blankField(), label: "2nd look!" }, blankField())

    expect(specFromDraft(draft).sections[0].fields.map((f) => f.key)).toEqual(["body", "body_2", "n_2nd_look", "field_4"])
  })

  it("keeps a loaded part's key when it is renamed", () => {
    const draft = draftFromSpec(SPEC, "check_in")
    draft.sections[0].fields[0].label = "Summary"

    expect(specFromDraft(draft).sections[0].fields[0].key).toBe("body")
  })

  it("drops blank choices, and a free-text input's choices", () => {
    const draft = draftFromSpec(SPEC, "check_in")
    draft.inputs[0].options = ["Office", "", " Video "]
    draft.inputs.push({ ...draft.inputs[0], uid: "x", key: "note", kind: "text", options: ["stale"] })

    expect(specFromDraft(draft).inputs.map((i) => i.options)).toEqual([["Office", "Video"], []])
  })

  it("picks a free slug for a new type and keeps a saved type's own", () => {
    const fresh = { ...blankDraft(), label: "Psychiatric follow-up visit for adults" }
    expect(slugFor(fresh, [])).toBe("psychiatric_follow_up_v")
    expect(slugFor({ ...fresh, preferredSlug: "follow_up" }, ["custom.follow_up"])).toBe("follow_up_2")
    expect(slugFor({ ...fresh, slug: "mine" }, ["custom.mine"])).toBe("mine")
    expect(keyFromLabel("  Mood & affect ")).toBe("mood_affect")
  })

  it("places validation messages by their path into the spec", () => {
    const error = new ApiError("UNKNOWN_ERROR", "failed", {
      validation: [
        { loc: ["body", "spec", "inputs", 0], msg: "Value error, input 'where' is a choice and needs at least two options" },
        { loc: ["body", "spec"], msg: "Value error, duplicate section keys" },
      ],
    })

    expect(fieldErrorsFrom(error, ["body", "spec"])).toEqual({
      "inputs.0": ["input 'where' is a choice and needs at least two options"],
      "": ["duplicate section keys"],
    })
    expect(fieldErrorsFrom(new ApiError("X", "Not allowed"))).toEqual({ "": ["Not allowed"] })
  })
})

describe("importSpec", () => {
  it("accepts a bare spec or one wrapped in {spec}, filling defaults", () => {
    const bare = importSpec(JSON.stringify({ label: "A", sections: [{ key: "s", label: "S", fields: [{ label: "F" }] }] }))
    expect(bare).toEqual({
      spec: {
        label: "A",
        description: "",
        system_prompt: "",
        user_template: null,
        sections: [{ key: "s", label: "S", fields: [{ key: "", label: "F", kind: "text", ai_hint: "" }] }],
        inputs: [],
      },
    })
    expect(importSpec(JSON.stringify({ spec: SPEC }))).toEqual({ spec: SPEC })
  })

  it("refuses text that is not a note type", () => {
    expect(importSpec("[]")).toEqual({ error: expect.stringContaining("doesn't look like a note type") })
    expect(importSpec("{")).toEqual({ error: "That isn't valid JSON." })
  })
})
