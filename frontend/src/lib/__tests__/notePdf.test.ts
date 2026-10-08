// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What a note's PDF prints, for every built-in type (the ones written as
 * specs included) and a practice's own type. Built-in layouts come from
 * `src/test/fixtures/builtinNoteTypes.json`, which the server's serializer
 * writes (backend `scripts/regen_builtin_note_types.py`).
 */

import { describe, expect, it } from "vitest"
import builtinNoteTypes from "@/test/fixtures/builtinNoteTypes.json"
import { narrativeNotePdf, schemaNotePdf, visitPdfLines, type PDFNoteLayout } from "../notePdf"
import { peopleWords } from "../peopleTerm"
import { soapNotePdf } from "../utils/pdfExport"
import type { SchemaSectionValues } from "@/types/sessions"
import type { VisitTimes } from "@/types/visitTimes"

const BUILTINS = builtinNoteTypes as unknown as Array<PDFNoteLayout & { key: string }>
// SOAP and Narrative have their own layouts on screen, and their own PDFs.
const SCHEMA_BUILTINS = BUILTINS.filter((t) => t.key !== "soap" && t.key !== "narrative")

/** A value in every field, named after where it sits. */
function filled(layout: PDFNoteLayout): Record<string, SchemaSectionValues> {
  const sections: Record<string, SchemaSectionValues> = {}
  for (const section of layout.sections) {
    const values: SchemaSectionValues = {}
    for (const field of section.fields) {
      const where = `${section.key}.${field.key}`
      values[field.key] =
        field.kind === "list"
          ? [`${where} one`, `${where} two`]
          : field.kind === "diagnoses"
            ? [{ label: `${where} dx`, code: "F41.1", status: "active" }]
            : field.kind === "structured"
              ? { score: 7 }
              : `${where} text`
    }
    sections[section.key] = values
  }
  return sections
}

/** Every section of the note itself: a review-only one is printed nowhere. */
function expectEveryFieldInOrder(layout: PDFNoteLayout) {
  const pdf = schemaNotePdf(layout, filled(layout))
  const printed = layout.sections.filter((s) => !s.review_only)
  expect(pdf.title).toBe(layout.label)
  expect(pdf.sections.map((s) => s.title)).toEqual(printed.map((s) => s.label))
  printed.forEach((section, i) => {
    const blocks = pdf.sections[i].blocks
    expect(blocks.map((b) => b.label)).toEqual(section.fields.map((f) => f.label))
    section.fields.forEach((field, j) => {
      const where = `${section.key}.${field.key}`
      const expected =
        field.kind === "list"
          ? `- ${where} one\n- ${where} two`
          : field.kind === "diagnoses"
            ? `- ${where} dx (F41.1), active`
            : field.kind === "structured"
              ? JSON.stringify({ score: 7 }, null, 2)
              : `${where} text`
      expect(blocks[j].content).toBe(expected)
    })
  })
}

describe("schemaNotePdf", () => {
  it("has every built-in schema type to render, the ones written as specs included", () => {
    expect(SCHEMA_BUILTINS.map((t) => t.key)).toEqual(
      expect.arrayContaining([
        "dap",
        "birp",
        "girp",
        "intake",
        "treatment_plan",
        "safety_plan",
        "psychiatric_evaluation",
        "psychiatric_follow_up",
      ]),
    )
  })

  it.each(SCHEMA_BUILTINS.map((t) => [t.key, t] as const))(
    "prints every section and field of %s in order, with its labels",
    (_key, layout) => expectEveryFieldInOrder(layout),
  )

  it.each(["psychiatric_follow_up", "psychiatric_evaluation"])(
    "prints no medical decision making section for %s, only the codes in the visit details",
    (key) => {
      const layout = BUILTINS.find((t) => t.key === key)!
      const values = filled(layout)
      values.encounter.visit_details = "E/M code: 99214. Psychotherapy add-on code: 90833."

      const pdf = schemaNotePdf(layout, values)

      expect(layout.sections.map((s) => s.key)).toContain("mdm")
      expect(pdf.sections.map((s) => s.title)).not.toContain("Medical decision making")
      expect(JSON.stringify(pdf)).not.toContain("mdm.problems_addressed")
      expect(pdf.sections[0].blocks[0]).toEqual({
        label: "Visit details",
        content: "E/M code: 99214. Psychotherapy add-on code: 90833.",
      })
    },
  )

  it("prints a practice type's stated diagnoses one per line, each as stated", () => {
    const layout: PDFNoteLayout = {
      label: "Medication follow-up",
      sections: [
        {
          key: "assessment",
          label: "Assessment",
          fields: [
            { key: "diagnoses", label: "Diagnoses", kind: "diagnoses" },
            { key: "formulation", label: "Formulation", kind: "text" },
          ],
        },
      ],
    }
    const pdf = schemaNotePdf(layout, {
      assessment: {
        diagnoses: [
          { label: "Major depressive disorder, recurrent", code: "F33.1", status: "improving" },
          { label: "Insomnia", code: null, status: null },
          "Generalized anxiety disorder",
        ],
        formulation: "Mood steadier on the current dose.",
      },
    })
    expect(pdf.sections[0].blocks).toEqual([
      {
        label: "Diagnoses",
        content:
          "- Major depressive disorder, recurrent (F33.1), improving\n- Insomnia\n- Generalized anxiety disorder",
      },
      { label: "Formulation", content: "Mood steadier on the current dose." },
    ])
  })

  it("leaves out empty fields, says so for an empty section, and prints values as stored", () => {
    const layout: PDFNoteLayout = {
      label: "Follow-up",
      sections: [
        {
          key: "risk",
          label: "Risk",
          fields: [
            { key: "ideation", label: "Ideation", kind: "text" },
            { key: "plan", label: "Safety plan", kind: "text" },
            { key: "factors", label: "Factors", kind: "list" },
          ],
        },
        { key: "labs", label: "Labs", fields: [{ key: "ordered", label: "Ordered", kind: "list" }] },
      ],
    }
    const pdf = schemaNotePdf(layout, {
      risk: { ideation: "Not stated.", plan: "  ", factors: [] },
    })
    expect(pdf.sections).toEqual([
      { title: "Risk", blocks: [{ label: "Ideation", content: "Not stated." }] },
      { title: "Labs", blocks: [{ label: null, content: "No content" }] },
    ])
  })
})

describe("narrativeNotePdf and soapNotePdf", () => {
  it("prints a narrative note's body", () => {
    expect(narrativeNotePdf("Steadier this week.")).toEqual({
      title: "Narrative Note",
      sections: [{ title: "", blocks: [{ label: null, content: "Steadier this week." }] }],
    })
  })

  it("prints SOAP's four sections, each sub-field under its label", () => {
    const pdf = soapNotePdf({
      subjective: "**Mood/Affect:** Brighter",
      objective: "Calm",
      assessment: "",
      plan: "**Next Steps:**\n- Walk daily",
    })
    expect(pdf.title).toBe("SOAP Note")
    expect(pdf.sections).toEqual([
      { title: "Subjective", blocks: [{ label: "Mood/Affect", content: "Brighter" }] },
      { title: "Objective", blocks: [{ label: null, content: "Calm" }] },
      { title: "Assessment", blocks: [] },
      { title: "Plan", blocks: [{ label: "Next Steps", content: "- Walk daily" }] },
    ])
  })
})

describe("visitPdfLines", () => {
  const people = peopleWords("clients")
  const base: VisitTimes = {
    started_at: "2026-10-06T15:00:00Z",
    ended_at: "2026-10-06T15:55:00Z",
    total_minutes: 55,
    recording_started_at: "2026-10-06T15:00:00Z",
    client_present_end_seconds: 3000,
    clinician_addendum_seconds: 180,
    psychotherapy: null,
    total_with_documentation_minutes: null,
  }
  const window = {
    offered: true,
    end_seconds: 3000,
    turns: [],
    candidates: [],
    stated_clock_time: null,
    confirmed_start_seconds: 720,
    confirmed_minutes: 38,
    window_text: "11:12 AM to 11:50 AM, 38 minutes",
    dictated_time: null,
    disagrees: false,
  }

  it("prints the visit, the time the client was present and the confirmed psychotherapy minutes", () => {
    expect(visitPdfLines({ ...base, psychotherapy: window }, "America/New_York", people)).toEqual([
      "Started 11:00 AM · Ended 11:55 AM · 55 min",
      "Client present until 11:50 AM · Your dictated addendum: 3 min",
      "Psychotherapy time: 11:12 AM to 11:50 AM, 38 minutes · 38–52 minutes",
    ])
  })

  it("leaves out psychotherapy minutes the clinician hasn't confirmed", () => {
    const unconfirmed = { ...window, confirmed_start_seconds: null, confirmed_minutes: null }
    expect(
      visitPdfLines({ ...base, psychotherapy: unconfirmed }, "America/New_York", people),
    ).not.toContainEqual(expect.stringContaining("Psychotherapy"))
  })

  it("prints the total time with documentation where the visit has one", () => {
    expect(
      visitPdfLines(
        { ...base, total_with_documentation_minutes: 62 },
        "America/New_York",
        people,
      ),
    ).toContain("Total time on this date, including documentation: 62 min")
  })

  it("prints nothing for a visit with no recorded times", () => {
    expect(
      visitPdfLines(
        {
          ...base,
          started_at: null,
          ended_at: null,
          total_minutes: null,
          recording_started_at: null,
          client_present_end_seconds: null,
          clinician_addendum_seconds: null,
        },
        "America/New_York",
        people,
      ),
    ).toEqual([])
  })
})
