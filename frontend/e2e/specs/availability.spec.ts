// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { test, expect } from "../fixtures/auth"
import { giveAvailabilityRule } from "../fixtures/scenarios"

type Slots = { slots: Array<{ start: string; end: string }> }

function nextFriday(): string {
  const date = new Date()
  date.setUTCDate(date.getUTCDate() + ((5 - date.getUTCDay() + 7) % 7 || 7))
  return date.toISOString().slice(0, 10)
}

test("the working-hours selects show the longest time label in full, on desktop and phone", async ({
  api,
  signedInPage: page,
}) => {
  // "12:30 PM" is the widest label the grid offers. A select sized for the
  // old shorter labels clipped it, and every other time, mid-letter.
  const sunday = await giveAvailabilityRule(api, "working_hours", {
    day_of_week: 6,
    start: "12:30",
    end: "21:30",
  })

  try {
    for (const viewport of [
      { width: 1440, height: 900 },
      { width: 375, height: 812 },
    ]) {
      await page.setViewportSize(viewport)
      await page.goto("/dashboard/settings/availability")

      const sundayStart = page.getByRole("combobox", { name: "Sunday start" })
      await expect(sundayStart).toHaveText("12:30 PM")
      await expect(page.getByRole("combobox", { name: "Sunday end" })).toHaveText("9:30 PM")

      const triggers = page.getByTestId("working-hours-grid").getByRole("combobox")
      for (const trigger of await triggers.all()) {
        const fit = await trigger.evaluate((el) => {
          const value = el.querySelector<HTMLElement>("[data-slot=select-value]")
          const grid = el.closest("[data-testid=working-hours-grid]")
          return {
            label: value?.textContent ?? "",
            clipped: !value || value.scrollWidth > value.clientWidth,
            right: el.getBoundingClientRect().right,
            gridRight: grid?.getBoundingClientRect().right ?? 0,
          }
        })
        expect(fit.clipped, `"${fit.label}" is cut off at ${viewport.width}px`).toBe(false)
        // Measured against the grid, not the window: the app shell's sidebar
        // stays open at phone width, so the card is narrower than the screen.
        expect(fit.right, `"${fit.label}" spills out of the card at ${viewport.width}px`).toBeLessThanOrEqual(
          fit.gridRight + 1,
        )
      }
    }
  } finally {
    await api.delete(`/api/availability/rules/${sunday.id}`)
  }
})

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
