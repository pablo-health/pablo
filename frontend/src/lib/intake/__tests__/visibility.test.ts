// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Whether a question is shown, run against the server's own table of cases.
 *
 * `fixtures/intake_visibility_cases.json` is a copy of
 * `backend/tests/fixtures/intake_visibility_cases.json`, written by
 * `scripts/sync_intake_visibility_fixtures.py` and pinned identical by a
 * test on the backend side. Python runs the same table in
 * `backend/tests/test_intake_visibility.py`. That is the whole point of the
 * arrangement: the browser deciding what to draw and the server deciding
 * what a form still needs have to agree, and agreeing today is not the same
 * as being unable to disagree tomorrow.
 *
 * What is tested here beyond the table is the part the table cannot carry:
 * reading a stored rule off an item's settings, where a settings blob
 * written by an older editor is an ordinary thing to find.
 */

import { describe, expect, it } from "vitest"
import { evaluate, ruleOf, type VisibilityItem, type VisibleWhen } from "../visibility"
import fixtures from "./fixtures/intake_visibility_cases.json"

interface FixtureItem {
  key: string
  visible_when?: VisibleWhen
  instrument_items?: number | null
}

interface FixtureCase {
  name: string
  items: FixtureItem[]
  answers: Record<string, unknown>
  visible: Record<string, boolean>
}

const cases = fixtures.cases as unknown as FixtureCase[]

function itemsOf(one: FixtureCase): VisibilityItem[] {
  return one.items.map((item) => ({
    key: item.key,
    rule: item.visible_when ?? null,
    instrumentItems: item.instrument_items ?? null,
  }))
}

describe("evaluate, against the shared rule table", () => {
  it("has a table to run", () => {
    // A fixture file that failed to resolve would otherwise pass silently
    // as zero cases, which is the one way this suite could lie.
    expect(cases.length).toBeGreaterThan(20)
  })

  for (const one of cases) {
    it(one.name, () => {
      expect(evaluate(itemsOf(one), one.answers)).toEqual(one.visible)
    })
  }
})

describe("evaluate, on shapes the table would not show clearly", () => {
  it("answers nothing for a form with no questions", () => {
    expect(evaluate([], {})).toEqual({})
  })

  it("does not read a yes as a one", () => {
    const rule: VisibleWhen = { item_key: "agrees", op: "eq", value: 1 }
    const items: VisibilityItem[] = [
      { key: "agrees", rule: null },
      { key: "detail", rule },
    ]
    expect(evaluate(items, { agrees: { yes: true } }).detail).toBe(false)
  })

  it("does not order a number against a date", () => {
    const rule: VisibleWhen = { item_key: "started", op: "gte", value: 3 }
    const items: VisibilityItem[] = [
      { key: "started", rule: null },
      { key: "detail", rule },
    ]
    expect(evaluate(items, { started: { value: "2020-01-01" } }).detail).toBe(false)
  })

  it("treats a day that never happened as no date at all", () => {
    const rule: VisibleWhen = { item_key: "started", op: "gte", value: "2020-01-01" }
    const items: VisibilityItem[] = [
      { key: "started", rule: null },
      { key: "detail", rule },
    ]
    expect(evaluate(items, { started: { value: "2021-02-31" } }).detail).toBe(false)
  })

  it("settles nothing about a measure whose size it was not told", () => {
    const rule: VisibleWhen = { item_key: "phq2", op: "score_gte", value: 1 }
    const items: VisibilityItem[] = [
      { key: "phq2", rule: null },
      { key: "phq9", rule },
    ]
    expect(evaluate(items, { phq2: { item_scores: { "1": 3, "2": 3 } } }).phq9).toBe(false)
  })

  it("does not read an answer that is not an answer", () => {
    const rule: VisibleWhen = { item_key: "drinks", op: "answered" }
    const items: VisibilityItem[] = [
      { key: "drinks", rule: null },
      { key: "detail", rule },
    ]
    expect(evaluate(items, { drinks: null }).detail).toBe(false)
  })
})

describe("ruleOf", () => {
  const rule: VisibleWhen = { item_key: "phq2", op: "score_gte", value: 3 }

  it("reads a stored rule off an item's settings", () => {
    expect(ruleOf({ code: "phq9", visible_when: rule })).toEqual(rule)
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
