// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"

import {
  isThin,
  moveBoundary,
  nearestTurn,
  relabel,
  remainderMinutes,
  runsOf,
  therapyMinutes,
  type Labels,
} from "../therapyTimeline"
import type { RecordingTurn } from "@/types/visitTimes"

function turns(...starts: number[]): RecordingTurn[] {
  return starts.map((s, i) => ({
    seconds: s,
    end_seconds: starts[i + 1] ?? s + 60,
    speaker: "Therapist",
    text: "",
    label: null,
  }))
}

describe("therapy timeline", () => {
  const t = turns(0, 60, 120, 180, 240)
  const labels: Labels = ["admin", "therapy", "therapy", "medication_management", null]

  it("groups turns into runs, unlabeled ones as their own", () => {
    expect(runsOf(t, labels).map((r) => [r.label, r.first, r.last, r.start, r.end])).toEqual([
      ["admin", 0, 0, 0, 60],
      ["therapy", 1, 2, 60, 180],
      ["medication_management", 3, 3, 180, 240],
      ["unattributed", 4, 4, 240, 300],
    ])
  })

  it("adds up the therapy turns, rounded down", () => {
    expect(therapyMinutes(t, labels)).toBe(2)
    expect(therapyMinutes(t, [null, null, null, null, null])).toBe(0)
  })

  it("relabels a whole run", () => {
    const [, therapy] = runsOf(t, labels)
    expect(relabel(labels, therapy, "screening_risk")).toEqual([
      "admin",
      "screening_risk",
      "screening_risk",
      "medication_management",
      null,
    ])
  })

  it("moves a boundary later to grow the run before it, earlier to grow the one after", () => {
    // The therapy run starts at turn 1.
    expect(moveBoundary(labels, 1, 2)).toEqual(["admin", "admin", "therapy", "medication_management", null])
    // The medication run starts at turn 3.
    expect(moveBoundary(labels, 3, 2)).toEqual([
      "admin",
      "therapy",
      "medication_management",
      "medication_management",
      null,
    ])
    expect(moveBoundary(labels, 3, 3)).toBe(labels)
  })

  it("finds the turn nearest a point, within bounds", () => {
    expect(nearestTurn(t, 130, 1, 4)).toBe(2)
    expect(nearestTurn(t, 0, 1, 4)).toBe(1)
  })

  it("leaves the rest of the time with the client to the medical visit, and flags it when thin", () => {
    expect(remainderMinutes(50 * 60, 34)).toBe(16)
    expect(remainderMinutes(20 * 60, 34)).toBe(0)
    expect(isThin(4)).toBe(true)
    expect(isThin(5)).toBe(false)
    expect(isThin(16)).toBe(false)
  })
})
