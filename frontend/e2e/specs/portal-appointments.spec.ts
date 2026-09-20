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
import type { ApiClient } from "../fixtures/api"
import {
  givePortalContactDetails,
  givePortalInvitation,
  signInToPortal,
} from "../fixtures/portal"
import {
  giveSelfBookableType,
  giveWorkingHoursAllWeek,
  letExistingClientsSelfBook,
} from "../fixtures/portal-appointments"
import { givePatient, type Patient } from "../fixtures/scenarios"

interface ClinicianAppointment {
  id: string
  start_at: string
  status: string
  patient_id: string | null
}

/**
 * This patient's appointments, as the clinician who owns the diary sees
 * them.
 *
 * The clinician route is a range query over the whole practice, so the range
 * is wide enough to cover anything a spec here books and the rows are
 * narrowed to the one patient afterwards. A worker shares its practice with
 * nothing else, but filtering by patient keeps the assertion about the
 * patient rather than about the worker.
 */
/** A patient of this practice who can be invited into the portal. */
async function givePortalPatient(api: ApiClient): Promise<{
  patient: Patient
  email: string
  phone: string
}> {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone, date_of_birth: "1988-04-02" })
  return { patient, email, phone }
}

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

/**
 * This patient's appointments once the diary holds `count` of them.
 *
 * A booking is committed before the portal answers, but the clinician's range
 * query has been observed answering 200 with nothing in it immediately after —
 * so reading once turns a settling window into a failed assertion about the
 * wrong thing. Polling for the count this step expects keeps the assertions
 * that follow about what the appointments ARE.
 */
async function diaryOnceItHolds(
  api: ApiClient,
  patientId: string,
  count: number,
): Promise<ClinicianAppointment[]> {
  await expect.poll(async () => (await diaryFor(api, patientId)).length).toBe(count)
  return diaryFor(api, patientId)
}

async function diaryFor(api: ApiClient, patientId: string): Promise<ClinicianAppointment[]> {
  const start = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString()
  const end = new Date(Date.now() + 90 * 24 * 60 * 60 * 1000).toISOString()
  const all = await api.get<{ data: ClinicianAppointment[] }>(
    `/api/appointments?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
  )
  return all.data.filter((appointment) => appointment.patient_id === patientId)
}

test.describe("portal appointments", () => {
  /**
   * This one also proves something none of its assertions mention: that the
   * practice knows which account owns it.
   *
   * `owner_session` in `backend/app/routes/patient_booking.py` resolves the
   * clinician whose diary is in question from
   * `platform.practices.owner_user_id`, so every route walked below refuses
   * with `NO_CLINICIAN` until that column is filled. Nothing filled it when
   * this spec was first written, and the integration suite did not notice
   * because its fixture set the column by hand. The clinician here signs in
   * through the product's own login, into a practice registered before they
   * existed — which is precisely the path that has to record it.
   */
  test("a patient books a time, moves it, and cancels it", async ({ api, page }) => {
    // --- the practice opens its diary ---------------------------------------
    await giveWorkingHoursAllWeek(api)
    const type = await giveSelfBookableType(api, `Therapy session ${Date.now().toString(36)}`)
    await letExistingClientsSelfBook(api)

    const { patient, email, phone } = await givePortalPatient(api)
    await signInToPortal(page, await givePortalInvitation(api, patient.id, email, phone))

    // --- nothing yet --------------------------------------------------------
    await expect(page.getByTestId("appointments-none-upcoming")).toBeVisible()
    // The invitation to book is there because the practice said so, and the
    // read-only notice is not.
    await expect(page.getByTestId("portal-appointments-book")).toBeVisible()
    await expect(page.getByTestId("portal-appointments-booking-off")).toBeHidden()

    // --- book ---------------------------------------------------------------
    await page.getByTestId("portal-appointments-book").click()
    await expect(page.getByTestId("appointments-slot-picker")).toBeVisible()

    // Walk forward past the practice's notice period, which is 24 hours by
    // default — today and tomorrow are inside it, so the first day that can
    // offer anything is two out.
    await page.getByTestId("appointments-slots-next").click()
    await page.getByTestId("appointments-slots-next").click()
    await expect(page.getByTestId("appointments-slot").first()).toBeVisible()

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

    // Days further out again, so the new time is a different one. The opening
    // is taken without reading it first, which is not carelessness: above, the
    // first two days are inside the notice period and offer nothing, so "wait
    // until an opening is on screen" genuinely waits for the day walked to.
    // Here the picker opens on a day that already has openings, the same wait
    // is satisfied by the grid already there, and text read back can belong to
    // a day since walked past. Whichever opening is taken, it is not the one
    // being given up, and that is what the assertions below are about.
    await page.getByTestId("appointments-slots-next").click()
    await page.getByTestId("appointments-slots-next").click()
    await page.getByTestId("appointments-slots-next").click()
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
