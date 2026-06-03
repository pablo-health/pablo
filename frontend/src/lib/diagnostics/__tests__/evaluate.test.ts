// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Client evaluator tests (PABLO-6xj)
 *
 * The client preview must agree with the backend "criteria" strategy
 * (`app.diagnostics.evaluator`): count thresholds per group, an optional
 * cardinal requirement, and all gates attested. Reason strings are asserted
 * verbatim because the saved record's `unmet_reasons` come from the same logic.
 */

import { describe, it, expect } from "vitest"
import { evaluateDefinition } from "../evaluate"
import type { DiagnosticDefinition } from "@/types/diagnoses"

const MDD: DiagnosticDefinition = {
  code: "mdd",
  version: 1,
  display_name: "Major Depressive Disorder",
  evaluator_type: "criteria",
  suggested_icd10: "F32.9",
  criterion_groups: [
    {
      key: "A",
      label: "Core symptoms",
      min_met: 5,
      require_cardinal: true,
      criteria: [
        { key: "A1", label: "Depressed mood", cardinal: true },
        { key: "A2", label: "Loss of interest", cardinal: true },
        { key: "A3", label: "Appetite change", cardinal: false },
        { key: "A4", label: "Sleep change", cardinal: false },
        { key: "A5", label: "Psychomotor change", cardinal: false },
        { key: "A6", label: "Fatigue", cardinal: false },
      ],
    },
  ],
  gates: [
    { key: "duration", label: "Present about two weeks" },
    { key: "impairment", label: "Causes distress or impairment" },
  ],
  icd10_options: [
    { code: "F32.9", label: "MDD, single episode, unspecified" },
    { code: "F33.9", label: "MDD, recurrent, unspecified" },
  ],
}

const allTrue = (keys: string[]): Record<string, boolean> =>
  Object.fromEntries(keys.map((k) => [k, true]))

describe("evaluateDefinition", () => {
  it("reports the count shortfall and every unmet gate when empty", () => {
    const out = evaluateDefinition(MDD, {}, {})
    expect(out.meetsCriteria).toBe(false)
    expect(out.suggestedIcd10).toBeNull()
    expect(out.unmetReasons).toContain("Core symptoms: needs at least 5, 0 met")
    expect(out.unmetReasons).toContain("Not met: Present about two weeks")
    expect(out.unmetReasons).toContain("Not met: Causes distress or impairment")
  })

  it("meets criteria with 5 symptoms incl. a cardinal and all gates", () => {
    const out = evaluateDefinition(
      MDD,
      allTrue(["A1", "A3", "A4", "A5", "A6"]),
      allTrue(["duration", "impairment"]),
    )
    expect(out.meetsCriteria).toBe(true)
    expect(out.unmetReasons).toEqual([])
    expect(out.suggestedIcd10).toBe("F32.9")
  })

  it("fails the cardinal requirement even when the count is reached", () => {
    // A group whose count threshold is satisfied by non-cardinal criteria
    // alone — the cardinal rule must still block it.
    const def: DiagnosticDefinition = {
      ...MDD,
      criterion_groups: [
        {
          key: "A",
          label: "Core symptoms",
          min_met: 2,
          require_cardinal: true,
          criteria: [
            { key: "A1", label: "Depressed mood", cardinal: true },
            { key: "A2", label: "Appetite change", cardinal: false },
            { key: "A3", label: "Sleep change", cardinal: false },
          ],
        },
      ],
      gates: [],
    }
    // Two non-cardinal met: count (2 >= 2) passes, cardinal does not.
    const out = evaluateDefinition(def, allTrue(["A2", "A3"]), {})
    expect(out.meetsCriteria).toBe(false)
    expect(out.unmetReasons).toEqual([
      "Core symptoms: requires at least one core symptom",
    ])

    // Add the cardinal symptom → now it meets.
    const withCardinal = evaluateDefinition(def, allTrue(["A1", "A2"]), {})
    expect(withCardinal.meetsCriteria).toBe(true)
  })

  it("blocks on a single missing gate", () => {
    const out = evaluateDefinition(
      MDD,
      allTrue(["A1", "A2", "A3", "A4", "A5"]),
      allTrue(["duration"]), // impairment missing
    )
    expect(out.meetsCriteria).toBe(false)
    expect(out.unmetReasons).toEqual([
      "Not met: Causes distress or impairment",
    ])
    expect(out.suggestedIcd10).toBeNull()
  })

  it("treats only explicit true as met (false / missing do not count)", () => {
    const out = evaluateDefinition(
      MDD,
      { A1: true, A2: true, A3: false, A4: true, A5: true, A6: false },
      allTrue(["duration", "impairment"]),
    )
    // 4 true (A1,A2,A4,A5) — short of 5.
    expect(out.meetsCriteria).toBe(false)
    expect(out.unmetReasons).toContain("Core symptoms: needs at least 5, 4 met")
  })
})

describe("evaluateDefinition — checklist evaluator_type", () => {
  it("makes no determination and suggests no code", () => {
    const checklist: DiagnosticDefinition = { ...MDD, evaluator_type: "checklist" }
    // Even with every response satisfied, a checklist returns no verdict and no
    // suggested code — the clinician selects the specifier from the options.
    const out = evaluateDefinition(
      checklist,
      allTrue(["A1", "A2", "A3", "A4", "A5", "A6"]),
      allTrue(["duration", "impairment"]),
    )
    expect(out.meetsCriteria).toBeNull()
    expect(out.unmetReasons).toEqual([])
    expect(out.suggestedIcd10).toBeNull()
  })

  it("suggests no code with no responses recorded either", () => {
    const checklist: DiagnosticDefinition = { ...MDD, evaluator_type: "checklist" }
    const out = evaluateDefinition(checklist, {}, {})
    expect(out.meetsCriteria).toBeNull()
    expect(out.unmetReasons).toEqual([])
    expect(out.suggestedIcd10).toBeNull()
  })
})
