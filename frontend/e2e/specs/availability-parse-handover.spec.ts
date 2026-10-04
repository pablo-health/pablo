// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Describing your hours still answers quickly when the model's first call
 * goes wrong. The stack's stand-in for the model (scripts/fake_llm.py)
 * fails the first call for one sentence with a 503, and leaves the first
 * call for another hanging for longer than the backend waits on any call.
 * Either way the backend hands over to its next attempt rather than waiting
 * the first one out, so the reading arrives well inside the time a
 * clinician will sit on the hours step.
 */

import { expect, test } from "../fixtures/auth"

/** How long a clinician should ever wait for a reading of their hours. */
const BUDGET_MS = 8_000

type ParseResponse = {
  proposals: { rule_type: string; params: { day_of_week: number; start: string; end: string } }[]
  could_not_parse: string | null
}

const cases = [
  { what: "fails", sentence: "10 to 6 Monday to Thursday", start: "10:00", end: "18:00" },
  { what: "stalls", sentence: "8 to 4 Monday to Thursday", start: "08:00", end: "16:00" },
]

for (const { what, sentence, start, end } of cases) {
  test(`hours are read within the budget when the first call ${what}`, async ({ api }) => {
    const began = Date.now()
    const result = await api.post<ParseResponse>("/api/availability/rules/parse", {
      text: sentence,
    })
    const elapsed = Date.now() - began

    expect(result.could_not_parse).toBeNull()
    expect(result.proposals.map((p) => [p.rule_type, p.params])).toEqual(
      [0, 1, 2, 3].map((day) => ["working_hours", { day_of_week: day, start, end }]),
    )
    expect(elapsed, `answered in ${elapsed} ms`).toBeLessThan(BUDGET_MS)
  })
}
