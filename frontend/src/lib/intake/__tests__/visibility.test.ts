// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The visibility seam, pinned at the stub.
 *
 * The point of the test is the same as the point of the Python one beside
 * `every_item_visible`: the v1 answer is "shown", for every question and
 * every rule, so a form with rules on it behaves exactly as one without. The
 * day that stops being true, this test is what says so.
 */

import { describe, expect, it } from "vitest"
import { everyItemVisible, ruleOf, type VisibleWhen } from "../visibility"

const RULE: VisibleWhen = { item_key: "phq2", op: "score_gte", value: 3 }

describe("everyItemVisible", () => {
  it("shows a question with no rule on it", () => {
    expect(everyItemVisible(null, {})).toBe(true)
  })

  it("shows a question whose rule the answers do not satisfy", () => {
    expect(everyItemVisible(RULE, { phq2: { item_scores: { "1": 0, "2": 0 } } })).toBe(true)
  })
})

describe("ruleOf", () => {
  it("reads a stored rule off an item's settings", () => {
    expect(ruleOf({ code: "phq9", visible_when: RULE })).toEqual(RULE)
  })

  it("reads no rule where there is none", () => {
    expect(ruleOf({ code: "phq9" })).toBeNull()
  })

  it("ignores a stored shape that is not a rule", () => {
    // A settings blob written by an older editor is an ordinary thing to
    // find, and it is not a reason to stop drawing the form.
    expect(ruleOf({ visible_when: "always" })).toBeNull()
    expect(ruleOf({ visible_when: { op: "eq" } })).toBeNull()
  })
})
