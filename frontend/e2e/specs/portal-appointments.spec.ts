// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A patient books, moves and cancels their own appointment, in a browser.
 *
 * The unit tests prove each screen over a mocked client; this proves the part
 * only a real stack can — that the practice's policy reaches the portal, that
 * the openings offered are ones the engine will actually accept, and that a
 * booking made from the patient's side shows up on the clinician's diary.
 *
 * Both factors come from the stand-in their channel is wired to: the invite
 * link out of the mail server, the step-up code out of the text-message
 * gateway. Both are real sends through the real service; only the last hop is
 * a fake.
 *
 * The second test is the one that is easy to leave out and the one a patient
 * is most likely to meet: a practice that has not turned online booking on.
 * The portal must still show them their appointments and say how to reach the
 * practice, rather than offering a control that does nothing.
 */

import type { Locator, Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import { signInAs, signInToFreshPractice } from "../fixtures/freshPractice"
import {
  givePortalInvitation,
  givePortalSession,
  openPortalSection,
  signInToPortal,
} from "../fixtures/portal"
import {
  diaryFor,
  diaryOnceItHolds,
  givePortalPatient,
  giveSelfBookableType,
  giveWorkingHoursAllWeek,
  letExistingClientsSelfBook,
  walkToAnOpening,
} from "../fixtures/portal-appointments"
import { BACKEND_URL } from "../fixtures/stack"

/**
 * The upcoming list, and the list of what is over.
 *
 * Both draw the same row, so a bare `appointments-row` spans the two as soon
 * as anything has been cancelled — and a reschedule cancels something. The
 * upcoming list is the page's `<section>`; what is over lives in a `<details>`
 * that carries its own test id.
 */
function upcomingRows(page: Page): Locator {
  return page.getByTestId("appointments-list").locator("section").getByTestId("appointments-row")
}

function pastRows(page: Page): Locator {
  return page.getByTestId("appointments-past").getByTestId("appointments-row")
}

test.describe("portal appointments", () => {
  /**
   * This one also proves something none of its assertions mention: that the
   * practice knows which account owns it.
   *
   * `clinician_session` in `backend/app/routes/patient_booking.py` books with
   * the client's own clinician, found among the practice's members, and the
   * owner recorded in `platform.practices.owner_user_id` is one of them. The
   * clinician here signs in through the product's own login, into a practice
   * registered before they existed — the path that has to record it.
   */
  // The hours a test opened, taken away again whether or not it passed: they
  // live on the worker's clinician and would otherwise fence in later specs.
  let closeDiary: (() => Promise<void>) | null = null
  test.afterEach(async () => {
    await closeDiary?.()
    closeDiary = null
  })

  test("a patient books a time, moves it, and cancels it", async ({ api, page }) => {
    // --- the practice opens its diary ---------------------------------------
    closeDiary = (await giveWorkingHoursAllWeek(api)).cleanup
    await giveSelfBookableType(api, `Therapy session ${Date.now().toString(36)}`)
    await letExistingClientsSelfBook(api)

    const { patient, email, phone } = await givePortalPatient(api)
    await signInToPortal(page, await givePortalInvitation(api, patient.id, email, phone))
    await openPortalSection(page, "appointments")

    // --- nothing yet --------------------------------------------------------
    await expect(page.getByTestId("appointments-none-upcoming")).toBeVisible()
    // The invitation to book is there because the practice said so, and the
    // read-only notice is not.
    await expect(page.getByTestId("portal-appointments-book")).toBeVisible()
    await expect(page.getByTestId("portal-appointments-booking-off")).toBeHidden()

    // --- book ---------------------------------------------------------------
    await page.getByTestId("portal-appointments-book").click()
    await expect(page.getByTestId("appointments-slot-picker")).toBeVisible()

    const bookedDay = await walkToAnOpening(page)

    const firstSlot = await page.getByTestId("appointments-slot").first().innerText()
    await page.getByTestId("appointments-slot").first().click()

    await expect(page.getByTestId("appointments-confirm")).toBeVisible()
    await expect(page.getByTestId("appointments-confirm-when")).toContainText(firstSlot)
    await page.getByTestId("appointments-confirm-submit").click()

    await expect(page.getByTestId("appointments-confirmation")).toBeVisible()
    await page.getByTestId("appointments-confirmation-done").click()

    // --- and it is on the list ----------------------------------------------
    await expect(upcomingRows(page)).toHaveCount(1)
    await expect(upcomingRows(page).getByTestId("appointments-row-when")).toContainText(firstSlot)

    // The clinician's diary has it too, which is the half a portal-only
    // assertion cannot see.
    const afterBooking = await diaryOnceItHolds(api, patient.id, 1)
    expect(["pending", "confirmed"]).toContain(afterBooking[0].status)

    // What the list says now, so the assertion after the move is about the
    // time changing rather than about how a time is spelled. The two screens
    // format an instant differently — the confirmation writes the day out in
    // full, the list abbreviates it — so comparing their text would compare
    // the formatter.
    const bookedWhen = await upcomingRows(page).getByTestId("appointments-row-when").innerText()

    // --- move it ------------------------------------------------------------
    await page.getByTestId("appointments-row-reschedule").click()
    await expect(page.getByTestId("appointments-slot-picker")).toBeVisible()

    // A day past the one being given up, so the new time is a different one
    // whichever opening on it the patient takes. The picker reopens on the day
    // it first opened on, so the walk starts from there and skips everything
    // up to and including the booked day.
    await walkToAnOpening(page, bookedDay + 1)
    await page.getByTestId("appointments-slot").first().click()

    await expect(page.getByTestId("appointments-confirm")).toBeVisible()
    await page.getByTestId("appointments-confirm-submit").click()

    await expect(page.getByTestId("appointments-confirmation")).toBeVisible()
    await page.getByTestId("appointments-confirmation-done").click()

    // A move is a cancellation and a booking rather than an edit, and the
    // engine says so: the time given up survives as its own record, which is
    // what a late-change fee is charged against. So the patient has one
    // upcoming appointment at a time that is not the one they gave up, the
    // time they gave up is under past, and the id has changed.
    await expect(upcomingRows(page)).toHaveCount(1)
    await expect(upcomingRows(page).getByTestId("appointments-row-when")).not.toHaveText(bookedWhen)
    await expect(pastRows(page)).toHaveCount(1)
    await expect(pastRows(page).getByTestId("appointments-row-when")).toHaveText(bookedWhen)

    const afterMove = await diaryOnceItHolds(api, patient.id, 2)
    const stillOn = afterMove.filter((appointment) => appointment.status !== "cancelled")
    expect(stillOn).toHaveLength(1)
    expect(stillOn[0].id).not.toBe(afterBooking[0].id)
    expect(stillOn[0].start_at).not.toBe(afterBooking[0].start_at)
    expect(afterMove.filter((a) => a.id === afterBooking[0].id).map((a) => a.status)).toEqual([
      "cancelled",
    ])

    // --- cancel it ----------------------------------------------------------
    await page.getByTestId("appointments-row-cancel").click()

    // Far enough out that the notice period does not apply, so it goes
    // straight through and the row leaves the upcoming list.
    await expect(page.getByTestId("appointments-none-upcoming")).toBeVisible()
    await expect(page.getByTestId("appointments-past")).toBeVisible()

    // Both records — the time given up in the move, and the one just
    // cancelled — and nothing of this patient's still standing.
    const afterCancel = await diaryOnceItHolds(api, patient.id, 2)
    expect(afterCancel.map((appointment) => appointment.status)).toEqual([
      "cancelled",
      "cancelled",
    ])
  })

  test("a patient of a practice that books another way sees their list and how to reach it", async ({
    api,
    page,
  }) => {
    // No working hours here on purpose. This practice never opens its diary
    // to patients, so seeding availability would only constrain the
    // appointment the clinician makes below — which is what the practice
    // doing the booking looks like.
    await giveSelfBookableType(api, `Therapy session ${Date.now().toString(36)}`)
    // The switch stays off, which is where every practice starts.
    await letExistingClientsSelfBook(api, false)

    const { patient, email, phone } = await givePortalPatient(api)

    // An appointment the practice made, so the read-only list has something
    // in it — the state a patient of a phone-booking practice is actually in.
    const startAt = new Date()
    startAt.setUTCDate(startAt.getUTCDate() + 7)
    startAt.setUTCHours(14, 0, 0, 0)
    await api.post("/api/appointments", {
      patient_id: patient.id,
      title: "Session",
      start_at: startAt.toISOString(),
      end_at: new Date(startAt.getTime() + 50 * 60 * 1000).toISOString(),
      duration_minutes: 50,
      session_type: "individual",
    })

    await signInToPortal(page, await givePortalInvitation(api, patient.id, email, phone))
    await openPortalSection(page, "appointments")

    // The list is there and it is theirs.
    await expect(page.getByTestId("appointments-row")).toHaveCount(1)

    // And so is a sentence saying what to do instead. The assertion that
    // matters is the pair: a notice present AND no control that would refuse.
    await expect(page.getByTestId("portal-appointments-booking-off")).toBeVisible()
    await expect(page.getByTestId("portal-appointments-book")).toBeHidden()
    await expect(page.getByTestId("appointments-row-reschedule")).toBeHidden()
    await expect(page.getByTestId("appointments-row-cancel")).toBeHidden()
  })
})

/**
 * In a practice of two, a client books with their own clinician. Its own
 * practice (fixtures/freshPractice.ts, "self-booking"), seeded with a
 * colleague who is not the owner. Only the colleague opens hours, so any
 * opening the portal offers is the colleague's.
 */
test.describe("portal appointments with two clinicians", () => {
  const COLLEAGUE = {
    email: "e2e-fresh-self-booking-colleague@example.com",
    password: "E2e-fresh-self-booking-colleague-password-long-enough",
  }

  test("a colleague's client books into the colleague's diary, and a second client cannot take that time", async ({
    browser,
    page,
    request,
  }) => {
    const owner = await signInToFreshPractice(browser, "self-booking")
    const colleague = await signInAs(browser, COLLEAGUE.email, COLLEAGUE.password)
    const hours = await giveWorkingHoursAllWeek(colleague.api)
    try {
      await owner.api.put("/api/portal/settings", { enabled: true })
      await letExistingClientsSelfBook(owner.api)
      const kind = `Colleague session ${Date.now().toString(36)}`
      await giveSelfBookableType(colleague.api, kind)

      // --- the colleague's client books in the portal ------------------------
      const first = await givePortalPatient(colleague.api)
      await signInToPortal(
        page,
        await givePortalInvitation(colleague.api, first.patient.id, first.email, first.phone),
      )
      await openPortalSection(page, "appointments")
      await page.getByTestId("portal-appointments-book").click()
      await walkToAnOpening(page)
      await page.getByTestId("appointments-slot").first().click()
      await page.getByTestId("appointments-confirm-submit").click()
      await expect(page.getByTestId("appointments-confirmation")).toBeVisible()

      // On the colleague's diary, and not on the owner's.
      const [booked] = await diaryOnceItHolds(colleague.api, first.patient.id, 1)
      expect(await diaryFor(owner.api, first.patient.id)).toEqual([])

      // --- another of the colleague's clients, the same time -----------------
      const second = await givePortalPatient(colleague.api)
      const token = await givePortalSession(
        colleague.api,
        request,
        second.patient.id,
        second.email,
        second.phone,
      )
      const headers = { Authorization: `Bearer ${token}` }
      const options = await request.get(`${BACKEND_URL}/api/patient/booking/options`, { headers })
      const { practice_timezone: zone } = (await options.json()) as { practice_timezone: string }
      const day = new Intl.DateTimeFormat("en-CA", { timeZone: zone }).format(new Date(booked.start_at))

      const slots = await request.get(`${BACKEND_URL}/api/patient/booking/slots?date=${day}`, {
        headers,
      })
      expect(slots.status(), await slots.text()).toBe(200)
      const offered = ((await slots.json()) as { data: { start_at: string }[] }).data.map((slot) =>
        new Date(slot.start_at).getTime(),
      )
      expect(offered.length, "the colleague has other openings that day").toBeGreaterThan(0)
      expect(offered).not.toContain(new Date(booked.start_at).getTime())

      const taken = await request.post(`${BACKEND_URL}/api/patient/booking`, {
        headers,
        data: { session_type: kind, start_at: booked.start_at },
      })
      expect(taken.status(), await taken.text()).toBe(409)
      expect(await taken.text()).toContain("SLOT_TAKEN")
    } finally {
      await hours.cleanup()
      await owner.context.close()
      await colleague.context.close()
    }
  })
})
