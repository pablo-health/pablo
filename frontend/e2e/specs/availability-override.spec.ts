// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Booking into your own availability rules: asked, not refused.
 *
 * The story this pins down happened to a real therapist. They blocked
 * Fridays, went to book a Friday appointment for a client they had already
 * agreed to see, and the save was refused outright with no way through. They
 * deleted the rule to get the appointment booked — which is the wrong outcome
 * twice: a rule is guidance about what to OFFER, not a lock on the practice,
 * and nobody should have to dismantle a standing preference to make one
 * exception to it.
 *
 * The override dialog has unit tests, and they are good ones. They cannot
 * reach what is tested here: every one of them mocks the conflict check and
 * the save, so they prove the dialog reacts to a conflict the test handed it.
 * Whether the ENGINE still refuses a request carrying the flag, whether the
 * route threads it through, and whether the appointment actually lands, are
 * facts about the whole path — engine, route, database — and only the local
 * stack has all of them.
 *
 * What is deliberately NOT asserted here: the grid shading that renders
 * unavailable time. That is a pure computation over free slots and it has
 * its own unit tests (editorial/__tests__/unavailability.test.ts); asserting
 * a background layer's geometry through a browser would be brittle without
 * proving anything those tests do not already prove.
 */

import type { Page } from "@playwright/test"
import { test, expect } from "../fixtures/auth"
import {
  givePatient,
  giveAvailabilityRule,
  giveWorkingHours,
  markCalendarSetupComplete,
  type Patient,
} from "../fixtures/scenarios"
import type { ApiClient } from "../fixtures/api"

const BOOKS_AT = "10:00"
const OPENS_AT = "09:00"
const CLOSES_AT = "17:00"

/**
 * The practice's timezone, typed here for the same reason scheduling.spec.ts
 * pins it: the form sends what the BROWSER makes of "10:00", and the engine
 * asks whether that instant is inside practice-local working hours. On a UTC
 * runner those are different clocks, and the booking then trips an
 * out-of-hours conflict instead of the one this test is about — which still
 * opens a dialog, so the spec would pass while proving the wrong thing.
 */
const PRACTICE_TIMEZONE = "America/New_York"

test.use({ timezoneId: PRACTICE_TIMEZONE })

interface Appointment {
  id: string
  status: string
}

function tomorrow(): Date {
  const date = new Date()
  date.setDate(date.getDate() + 1)
  return date
}

function isoDate(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
    date.getDate(),
  ).padStart(2, "0")}`
}

/**
 * The engine's weekday numbering is Monday-based (`date.weekday()`), while
 * JS `getDay()` is Sunday-based. Getting this backwards blocks a different
 * day than the one booked, the save succeeds, and the spec fails on a
 * missing dialog with no hint why.
 */
function engineWeekday(date: Date): number {
  return (date.getDay() + 6) % 7
}

/**
 * A practice that works this weekday, and one rule covering it.
 *
 * Returns a `cleanup` the caller must run. Rules live on the user, and this
 * suite's tests share one: without the teardown, the soft-rule test inherits
 * the hard rules its siblings left behind, the dialog dutifully summarises
 * all of them, and the assertion fails describing a rule the test never made.
 * Order-dependence like that reads as flake and gets retried rather than
 * fixed.
 */
async function givePracticeThatBlocksTomorrow(
  api: ApiClient,
  enforcement: "hard" | "soft",
): Promise<{ patient: Patient; date: Date; cleanup: () => Promise<void> }> {
  const date = tomorrow()
  const patient = await givePatient(api)
  await markCalendarSetupComplete(api)
  const hours = await giveWorkingHours(api, engineWeekday(date), {
    start: OPENS_AT,
    end: CLOSES_AT,
  })
  const block = await giveAvailabilityRule(
    api,
    "block_day_of_week",
    { day_of_week: engineWeekday(date) },
    enforcement,
  )
  const cleanup = async () => {
    for (const rule of [block, hours]) {
      await api.delete(`/api/availability/rules/${rule.id}`)
    }
  }
  return { patient, date, cleanup }
}

/** Fill the new-appointment form for the blocked window, without saving. */
async function fillBookingForm(page: Page, patient: Patient, date: Date): Promise<void> {
  await page.goto("/dashboard/calendar")
  await page.getByRole("button", { name: /new appointment/i }).click()

  const patientPicker = page.getByRole("combobox", { name: "Patient" })
  await patientPicker.click()
  await patientPicker.fill(patient.last_name)
  await page
    .getByRole("option", { name: `${patient.last_name}, ${patient.first_name}` })
    .click()

  await page.getByLabel("Date", { exact: true }).fill(isoDate(date))
  await page.getByLabel("Time", { exact: true }).fill(BOOKS_AT)
}

/**
 * The appointments standing on this date.
 *
 * Cancelled ones are filtered out, and that is not tidiness: cancelling is
 * how this API deletes, so a sibling test's cleaned-up appointment is still
 * returned for the same date, in the same worker, under the same user. A
 * spec that asserted "no appointments here" would pass alone and fail in a
 * suite — the kind of failure that gets diagnosed as flake.
 */
async function appointmentsOn(api: ApiClient, date: Date): Promise<Appointment[]> {
  // `start`/`end` without an offset are read as wall-clock in the owner's
  // timezone, which is the same frame the booking was typed in.
  const day = isoDate(date)
  const page = await api.get<{ data: Appointment[] }>(
    `/api/appointments?start=${day}T00:00:00&end=${day}T23:59:59`,
  )
  return page.data.filter((appointment) => appointment.status !== "cancelled")
}

test.describe("booking into your own availability rules", () => {
  test("a hard rule asks, and confirming books the appointment", async ({
    signedInPage: page,
    api,
  }) => {
    const { patient, date, cleanup } = await givePracticeThatBlocksTomorrow(api, "hard")
    let appointment: Appointment | undefined
    try {
      await fillBookingForm(page, patient, date)

      await page.getByRole("button", { name: "Schedule", exact: true }).click()

      // Asked, not refused — and the reason is the therapist's own rule, in
      // the words the settings page uses for it.
      await expect(page.getByRole("dialog")).toContainText("Book this event anyway?")
      await expect(page.getByRole("dialog")).toContainText(
        "You've blocked that day of the week.",
      )

      // Waits for the POST, not for a SUCCESSFUL POST. If the engine goes
      // back to refusing an overridden booking, this resolves immediately
      // with the 422 and the next line says so — rather than timing out
      // three minutes later reporting the wait instead of the cause. That
      // failure mode has already cost this suite once; see the note in
      // scheduling.spec.ts.
      const created = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === "/api/appointments" &&
          response.request().method() === "POST",
      )
      await page.getByRole("button", { name: "Override this event", exact: true }).click()
      const response = await created
      expect(
        response.status(),
        `the overridden booking was refused: ${await response.text()}`,
      ).toBe(201)
      appointment = (await response.json()) as Appointment

      // The appointment is really there — the engine accepted a request it
      // would have refused a moment earlier.
      const booked = await appointmentsOn(api, date)
      expect(booked.map((a) => a.id)).toContain(appointment.id)
    } finally {
      if (appointment) await api.delete(`/api/appointments/${appointment.id}`)
      await cleanup()
    }
  })

  test("declining writes nothing, and keeps the form", async ({
    signedInPage: page,
    api,
  }) => {
    const { patient, date, cleanup } = await givePracticeThatBlocksTomorrow(api, "hard")
    try {
      await fillBookingForm(page, patient, date)

      let posted = false
      page.on("request", (request) => {
        if (
          request.method() === "POST" &&
          new URL(request.url()).pathname === "/api/appointments"
        ) {
          posted = true
        }
      })

      await page.getByRole("button", { name: "Schedule", exact: true }).click()
      await expect(page.getByRole("dialog")).toContainText("Book this event anyway?")
      await page.getByRole("button", { name: "Cancel", exact: true }).click()

      // Nothing was sent, nothing was written, and the therapist has not lost
      // what they typed — declining is a "not like this", not a "start again".
      expect(posted).toBe(false)
      expect(await appointmentsOn(api, date)).toHaveLength(0)
      await expect(page.getByLabel("Time", { exact: true })).toHaveValue(BOOKS_AT)
    } finally {
      await cleanup()
    }
  })

  test("a soft rule asks the same question instead of writing silently", async ({
    signedInPage: page,
    api,
  }) => {
    // The mirror of the hard-rule bug. A soft rule used to write the
    // appointment and hand back a warning afterwards, which is not a choice —
    // it is being told what you would have said if anyone had asked.
    const { patient, date, cleanup } = await givePracticeThatBlocksTomorrow(api, "soft")
    try {
      await fillBookingForm(page, patient, date)

      let posted = false
      page.on("request", (request) => {
        if (
          request.method() === "POST" &&
          new URL(request.url()).pathname === "/api/appointments"
        ) {
          posted = true
        }
      })

      await page.getByRole("button", { name: "Schedule", exact: true }).click()

      await expect(page.getByRole("dialog")).toContainText("Book this event anyway?")
      // Same dialog, same buttons; only the framing softens, because the rule
      // is a habit rather than a boundary.
      await expect(page.getByRole("dialog")).toContainText(
        "You usually don't book that day of the week.",
      )
      expect(posted).toBe(false)

      await page.getByRole("button", { name: "Cancel", exact: true }).click()
      expect(await appointmentsOn(api, date)).toHaveLength(0)
    } finally {
      await cleanup()
    }
  })

  test("the dialog offers no way to change the rule", async ({ signedInPage: page, api }) => {
    // An override is an exception to a standing rule, not a revision of it,
    // and a booking flow is not where standing policy gets changed. If an
    // "unblock Fridays" ever appears here, the therapist loses the rule for
    // every future Friday in order to book one.
    const { patient, date, cleanup } = await givePracticeThatBlocksTomorrow(api, "hard")
    try {
      await fillBookingForm(page, patient, date)
      await page.getByRole("button", { name: "Schedule", exact: true }).click()

      const dialog = page.getByRole("dialog")
      await expect(dialog).toContainText("Book this event anyway?")

      // "Close" is the dialog primitive's X. It dismisses; it is not a way to
      // change a rule, which is what this test is about.
      const buttons = await dialog.getByRole("button").allInnerTexts()
      expect(buttons.map((text) => text.trim()).filter(Boolean).sort()).toEqual([
        "Cancel",
        "Close",
        "Override this event",
      ])
      // No link out to settings either: following one mid-booking would lose
      // the form, and the rule would still be there when they came back.
      await expect(dialog.getByRole("link")).toHaveCount(0)

      const rulesBefore = await api.get<{ data: unknown[] }>("/api/availability/rules")
      await page.getByRole("button", { name: "Cancel", exact: true }).click()
      const rulesAfter = await api.get<{ data: unknown[] }>("/api/availability/rules")
      expect(rulesAfter.data).toHaveLength(rulesBefore.data.length)
    } finally {
      await cleanup()
    }
  })
})
