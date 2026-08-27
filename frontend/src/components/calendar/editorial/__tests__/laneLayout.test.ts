// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect } from "vitest"
import { assignLanes } from "../laneLayout"
import type { AppointmentResponse } from "@/types/scheduling"

function appt(id: string, startHour: number, endHour: number): AppointmentResponse {
  const day = "2026-05-13"
  return {
    id,
    user_id: "u",
    patient_id: "p",
    title: id,
    start_at: `${day}T${String(startHour).padStart(2, "0")}:00:00.000Z`,
    end_at: `${day}T${String(endHour).padStart(2, "0")}:00:00.000Z`,
    duration_minutes: (endHour - startHour) * 60,
    status: "confirmed",
    session_type: "individual",
    video_link: null,
    video_platform: null,
    notes: null,
    note_type: "soap",
    recurrence_rule: null,
    recurring_appointment_id: null,
    recurrence_index: null,
    is_exception: false,
    google_event_id: null,
    google_sync_status: null,
    session_id: null,
    created_at: "",
    updated_at: null,
  }
}

describe("assignLanes", () => {
  it("places non-overlapping events in lane 0", () => {
    const result = assignLanes([appt("a", 9, 10), appt("b", 11, 12)])
    expect(result).toHaveLength(2)
    expect(result[0]).toMatchObject({ lane: 0, laneCount: 1 })
    expect(result[1]).toMatchObject({ lane: 0, laneCount: 1 })
  })

  it("splits two overlapping events into 2 lanes", () => {
    const result = assignLanes([appt("a", 9, 11), appt("b", 10, 12)])
    expect(result.find((r) => r.appointment.id === "a")).toMatchObject({
      lane: 0,
      laneCount: 2,
    })
    expect(result.find((r) => r.appointment.id === "b")).toMatchObject({
      lane: 1,
      laneCount: 2,
    })
  })

  it("propagates cluster lane count to all members", () => {
    // 3 events all overlap each other -> all should report laneCount=3.
    const result = assignLanes([
      appt("a", 9, 12),
      appt("b", 10, 11),
      appt("c", 10, 12),
    ])
    expect(result.every((r) => r.laneCount === 3)).toBe(true)
  })

  it("reuses lanes when they free up", () => {
    // a:9-10, b:9:30-10:30, c:10:30-11. After a ends, c can take lane 0.
    const result = assignLanes([
      appt("a", 9, 10),
      {
        ...appt("b", 9, 10),
        id: "b",
        start_at: "2026-05-13T09:30:00.000Z",
        end_at: "2026-05-13T10:30:00.000Z",
      },
      {
        ...appt("c", 10, 11),
        id: "c",
        start_at: "2026-05-13T10:30:00.000Z",
        end_at: "2026-05-13T11:00:00.000Z",
      },
    ])
    const c = result.find((r) => r.appointment.id === "c")
    expect(c?.lane).toBe(0)
  })
})
