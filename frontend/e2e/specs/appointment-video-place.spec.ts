// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The appointment form says where a session happens and which link it gets.
 *
 * The form's unit tests mock the providers list and the save, so they prove
 * the form sends what it means to. What only the stack can show: that the
 * service the form names is the one the server uses, that the link is on the
 * appointment when the save returns, that "in person" leaves the visit with
 * no room, and that the clinician's last choice is kept on the server, where
 * another device reads it.
 *
 * The stack offers doxy.me and pasted links, so doxy.me stands in for the
 * default service here: a clinician's own room, addressed per appointment,
 * with no vendor call to fake.
 */

import type { Page, Response } from "@playwright/test"
import { test, expect } from "../fixtures/auth"
import {
  givePatient,
  giveWorkingHours,
  markCalendarSetupComplete,
  type AvailabilityRule,
  type Patient,
} from "../fixtures/scenarios"
import type { ApiClient } from "../fixtures/api"

const ROOM_URL = "https://doxy.me/e2e-video-place"
const PASTED_LINK = "https://teams.example.test/l/meetup-join/e2e"

/** Typed for the reason scheduling.spec.ts gives: the form sends browser time. */
const PRACTICE_TIMEZONE = "America/New_York"
test.use({ timezoneId: PRACTICE_TIMEZONE })

interface SavedAppointment {
  id: string
  place_of_service: string | null
  provider: string | null
  video_link: string | null
}

interface Preferences {
  default_video_platform: string | null
  last_place_of_service?: string | null
}

interface Providers {
  room_url: string | null
}

/**
 * Tomorrow on the practice's wall clock, as the form's date field takes it,
 * and its weekday in the engine's numbering (0 = Monday). Read through Intl
 * because the runner's own zone is not the practice's.
 */
function practiceTomorrow(): { date: string; engineWeekday: number } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: PRACTICE_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date())
  const part = (type: string) => Number(parts.find((p) => p.type === type)?.value)
  // A UTC-midnight Date used only as a calendar, so DST cannot skip a day.
  const day = new Date(Date.UTC(part("year"), part("month") - 1, part("day") + 1))
  return {
    date: day.toISOString().slice(0, 10),
    engineWeekday: (day.getUTCDay() + 6) % 7,
  }
}

/** Open a new appointment for this client, tomorrow at `time`, without saving. */
async function openBooking(page: Page, patient: Patient, time: string): Promise<void> {
  await page.goto("/dashboard/calendar")
  await page.getByRole("button", { name: /new appointment/i }).click()
  const picker = page.getByRole("combobox", { name: "Client" })
  await picker.click()
  await picker.fill(patient.first_name)
  await page.getByRole("option", { name: `${patient.last_name}, ${patient.first_name}` }).click()
  await page.getByLabel("Date", { exact: true }).fill(practiceTomorrow().date)
  await page.getByLabel("Time", { exact: true }).fill(time)
}

/** Press Schedule and return the appointment the POST created. */
async function schedule(page: Page): Promise<SavedAppointment> {
  const created = page.waitForResponse(
    (response: Response) =>
      new URL(response.url()).pathname === "/api/appointments" &&
      response.request().method() === "POST",
  )
  await page.getByRole("button", { name: "Schedule", exact: true }).click()
  // An override question would swallow the submit, and the wait above would
  // then run to the test timeout. Say so at once instead.
  const asked = page
    .getByText("Book this event anyway?")
    .waitFor({ timeout: 10_000 })
    .then(() => "asked" as const)
    .catch(() => "not asked" as const)
  const first = await Promise.race([created.then(() => "posted" as const), asked])
  expect(first, "the booking ran into an availability rule instead of saving").not.toBe("asked")
  const response = await created
  expect(response.status(), `the booking was refused: ${await response.text()}`).toBe(201)
  return (await response.json()) as SavedAppointment
}

test.describe("where the session happens, on the appointment form", () => {
  let saved: { preferences: Preferences; roomUrl: string | null }
  let patient: Patient
  let hours: AvailabilityRule
  const booked: string[] = []

  test.beforeEach(async ({ api }) => {
    const preferences = await api.get<Preferences>("/api/users/me/preferences")
    const providers = await api.get<Providers>("/api/telehealth/providers")
    saved = { preferences, roomUrl: providers.room_url }
    await markCalendarSetupComplete(api)
    // Declared, not inherited: booking outside working hours asks the
    // clinician first (see scheduling.spec.ts), and that question would
    // stand between the form and the save this spec reads.
    hours = await giveWorkingHours(api, practiceTomorrow().engineWeekday, {
      start: "08:00",
      end: "18:00",
    })
    // A clinician whose default service is their doxy.me room, who has never
    // said where a booking happens.
    await api.put("/api/telehealth/room-url", { room_url: ROOM_URL })
    await api.patch("/api/users/me/preferences", {
      default_video_platform: "doxy_me",
      last_place_of_service: null,
    })
    patient = await givePatient(api)
  })

  test.afterEach(async ({ api }) => {
    for (const id of booked.splice(0)) await api.delete(`/api/appointments/${id}`)
    await api.delete(`/api/availability/rules/${hours.id}`)
    // The worker's clinician is shared with the rest of the suite.
    await api.put("/api/telehealth/room-url", { room_url: saved.roomUrl })
    await api.patch("/api/users/me/preferences", {
      default_video_platform: saved.preferences.default_video_platform,
      last_place_of_service: saved.preferences.last_place_of_service ?? null,
    })
  })

  test("video names the service before the save, and the link is there after it", async ({
    signedInPage: page,
    api,
  }) => {
    await openBooking(page, patient, "10:00")
    const where = page.getByRole("radiogroup", { name: "Where" })
    // Nobody has said yet, so the form does not guess.
    await expect(where.getByRole("radio", { name: "Video" })).toHaveAttribute("aria-checked", "false")
    await expect(where.getByRole("radio", { name: "In person" })).toHaveAttribute(
      "aria-checked",
      "false",
    )

    await where.getByRole("radio", { name: "Video" }).click()
    await expect(page.getByTestId("video-link-promise")).toHaveText(
      /Doxy\.me link added when you save/,
    )
    const appointment = await schedule(page)
    booked.push(appointment.id)

    expect(appointment.place_of_service).toBe("02")
    expect(appointment.provider).toBe("doxy_me")
    expect(appointment.video_link).toMatch(/^https:\/\/doxy\.me\/e2e-video-place/)

    // Kept on the server, so the next booking on any device starts at Video.
    await expect
      .poll(async () =>
        (await api.get<Preferences>("/api/users/me/preferences")).last_place_of_service,
      )
      .toBe("02")
    await openBooking(page, patient, "12:00")
    await expect(
      page.getByRole("radiogroup", { name: "Where" }).getByRole("radio", { name: "Video" }),
    ).toHaveAttribute("aria-checked", "true")
  })

  test("in person saves no room", async ({ signedInPage: page }) => {
    await openBooking(page, patient, "13:00")
    await page.getByRole("radiogroup", { name: "Where" }).getByRole("radio", { name: "In person" }).click()
    await expect(page.getByLabel("Video link")).toHaveCount(0)

    const appointment = await schedule(page)
    booked.push(appointment.id)
    expect(appointment.place_of_service).toBe("11")
    expect(appointment.video_link).toBeNull()
    expect(appointment.provider).toBeNull()
  })

  test("a pasted link is that appointment's room", async ({ signedInPage: page }) => {
    await openBooking(page, patient, "15:00")
    await page.getByRole("radiogroup", { name: "Where" }).getByRole("radio", { name: "Video" }).click()
    await page.getByRole("button", { name: "Change" }).click()
    await page.getByRole("button", { name: "Paste a link" }).click()
    await page.getByLabel("Video link").fill(PASTED_LINK)

    const appointment = await schedule(page)
    booked.push(appointment.id)
    expect(appointment.place_of_service).toBe("02")
    expect(appointment.video_link).toBe(PASTED_LINK)
  })
})
