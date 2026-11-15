// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Record activity folds identical rows a few seconds apart into one line.
 * Display only: every row the server sent stays in exactly one group.
 */

import { describe, expect, it } from "vitest"

import type { RecordActivityItem } from "@/lib/api/patientActivity"
import { groupRecordActivity } from "../recordActivityGroups"
import { NEW_CLIENT_FIRST_VISIT } from "./recordActivityFixture"

function row(id: string, timestamp: string, overrides: Partial<RecordActivityItem> = {}) {
  return {
    id,
    timestamp,
    actor_type: "clinician",
    actor_name: "Sam Owner",
    is_you: true,
    action: "patient_viewed",
    resource_type: "patient",
    ...overrides,
  } satisfies RecordActivityItem
}

const shape = (entries: RecordActivityItem[]) =>
  groupRecordActivity(entries).map((g) => [g.rows[0].action, g.rows.length])

describe("groupRecordActivity", () => {
  it("reads a new client's first visit as 14 lines, not 31", () => {
    expect(NEW_CLIENT_FIRST_VISIT).toHaveLength(31)
    expect(shape(NEW_CLIENT_FIRST_VISIT)).toEqual([
      ["patient_activity_viewed", 1],
      ["patient_viewed", 4],
      ["patient_viewed", 5],
      ["session_viewed", 2],
      ["session_viewed", 2],
      ["note_signed", 1],
      ["session_viewed", 2],
      ["session_updated", 1],
      ["session_viewed", 2],
      ["session_created", 1],
      ["patient_viewed", 5],
      ["patient_ai_consent_viewed", 2],
      ["waitlist_viewed", 2],
      ["patient_created", 1],
    ])
  })

  it("keeps every row, once, in the server's order", () => {
    const flattened = groupRecordActivity(NEW_CLIENT_FIRST_VISIT).flatMap((g) => g.rows)
    expect(flattened.map((r) => r.id).sort()).toEqual(NEW_CLIENT_FIRST_VISIT.map((r) => r.id).sort())
    for (const group of groupRecordActivity(NEW_CLIENT_FIRST_VISIT)) {
      const times = group.rows.map((r) => Date.parse(r.timestamp))
      expect(times).toEqual([...times].sort((a, b) => b - a))
    }
  })

  it("does not change the rows it was given", () => {
    const before = structuredClone(NEW_CLIENT_FIRST_VISIT)
    groupRecordActivity(NEW_CLIENT_FIRST_VISIT)
    expect(NEW_CLIENT_FIRST_VISIT).toEqual(before)
  })

  it("folds rows up to five seconds from the newest, and no further", () => {
    const groups = groupRecordActivity([
      row("a", "2026-11-15T16:00:10.000Z"),
      row("b", "2026-11-15T16:00:05.000Z"),
      row("c", "2026-11-15T16:00:04.999Z"),
    ])
    expect(groups.map((g) => g.rows.map((r) => r.id))).toEqual([["a", "b"], ["c"]])
  })

  it("measures from the group's newest row, so a steady repeat does not chain", () => {
    const groups = groupRecordActivity([
      row("a", "2026-11-15T16:00:12.000Z"),
      row("b", "2026-11-15T16:00:09.000Z"),
      row("c", "2026-11-15T16:00:06.000Z"),
      row("d", "2026-11-15T16:00:03.000Z"),
    ])
    expect(groups.map((g) => g.rows.map((r) => r.id))).toEqual([
      ["a", "b"],
      ["c", "d"],
    ])
  })

  it("folds across another action in between, inside the window", () => {
    const groups = groupRecordActivity([
      row("a", "2026-11-15T16:00:10.000Z"),
      row("b", "2026-11-15T16:00:10.000Z", { action: "waitlist_viewed" }),
      row("c", "2026-11-15T16:00:09.000Z"),
    ])
    expect(groups.map((g) => g.rows.map((r) => r.id))).toEqual([["a", "c"], ["b"]])
  })

  it.each([
    ["someone else", { is_you: false, actor_name: "Riley Colleague" }],
    ["another person of the same kind", { actor_name: "Riley Colleague" }],
    ["another kind of actor", { actor_type: "patient", actor_name: null }],
    ["another action", { action: "patient_updated" }],
    ["another kind of record", { resource_type: "session" }],
  ] as const)("never folds in a row by %s", (_label, overrides) => {
    const groups = groupRecordActivity([
      row("a", "2026-11-15T16:00:10.000Z"),
      row("b", "2026-11-15T16:00:10.000Z", overrides),
    ])
    expect(groups).toHaveLength(2)
  })

  it("never folds a row whose time cannot be read", () => {
    const groups = groupRecordActivity([
      row("a", "not a time"),
      row("b", "not a time"),
      row("c", "2026-11-15T16:00:10.000Z"),
      row("d", "not a time"),
      row("e", "2026-11-15T16:00:09.000Z"),
    ])
    expect(groups.map((g) => g.rows.map((r) => r.id))).toEqual([["a"], ["b"], ["c", "e"], ["d"]])
  })

  it("does not fold a row the server sent out of order", () => {
    const groups = groupRecordActivity([
      row("a", "2026-11-15T16:00:10.000Z"),
      row("b", "2026-11-15T16:00:11.000Z"),
    ])
    expect(groups).toHaveLength(2)
  })

  it("loading an older page only adds to the end", () => {
    const first = groupRecordActivity(NEW_CLIENT_FIRST_VISIT.slice(0, 20))
    const both = groupRecordActivity(NEW_CLIENT_FIRST_VISIT)
    expect(both.slice(0, first.length - 1)).toEqual(first.slice(0, -1))
    expect(both[first.length - 1].id).toBe(first[first.length - 1].id)
  })
})
