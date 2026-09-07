// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { test, expect } from "../fixtures/auth"
import { giveAvailabilityRule } from "../fixtures/scenarios"

type Slots = { slots: Array<{ start: string; end: string }> }

function nextFriday(): string {
  const date = new Date()
  date.setUTCDate(date.getUTCDate() + ((5 - date.getUTCDay() + 7) % 7 || 7))
  return date.toISOString().slice(0, 10)
}

test("a weekday block removes only that day's available slots", async ({ api }) => {
  const friday = nextFriday()
  const saturdayDate = new Date(`${friday}T00:00:00Z`)
  saturdayDate.setUTCDate(saturdayDate.getUTCDate() + 1)
  const saturday = saturdayDate.toISOString().slice(0, 10)

  const fridayHours = await giveAvailabilityRule(api, "working_hours", {
    day_of_week: 4,
    start: "09:00",
    end: "17:00",
  })
  const saturdayHours = await giveAvailabilityRule(api, "working_hours", {
    day_of_week: 5,
    start: "09:00",
    end: "17:00",
  })
  let blockId: string | undefined

  try {
    const before = await api.get<Slots>(`/api/availability/slots?date=${friday}&duration=50`)
    expect(before.slots.length).toBeGreaterThan(0)

    blockId = (await giveAvailabilityRule(api, "block_day_of_week", { day_of_week: 4 })).id
    expect((await api.get<Slots>(`/api/availability/slots?date=${friday}&duration=50`)).slots).toEqual(
      [],
    )
    expect(
      (await api.get<Slots>(`/api/availability/slots?date=${saturday}&duration=50`)).slots.length,
    ).toBeGreaterThan(0)
  } finally {
    if (blockId) await api.delete(`/api/availability/rules/${blockId}`)
    await api.delete(`/api/availability/rules/${fridayHours.id}`)
    await api.delete(`/api/availability/rules/${saturdayHours.id}`)
  }
})
