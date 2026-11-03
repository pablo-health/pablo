// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The limits on a patient booking for themselves, end to end.
 *
 * `portal-appointments.spec.ts` walks the happy path: a patient books, moves
 * and cancels. This file is about what the practice's policy and the patient
 * principal hold back, and each test proves the refusal against a time the
 * clinician's own engine would offer — so what is refused is the rule under
 * test, never a time nobody would have offered anyway.
 *
 * - A practice that takes requests rather than bookings sees the patient's
 *   time arrive as pending, confirms it, and the patient then reads it as
 *   confirmed.
 * - A time inside the practice's minimum notice is refused and nothing lands
 *   in the diary, while a time past it books.
 * - One patient can neither see nor change another patient's appointment.
 * - With self-booking off for existing clients, the routes refuse rather than
 *   answering with an empty diary.
 *
 * The refusal paths go straight at `/api/patient/booking` with a stepped-up
 * portal session, because the portal never offers the times being refused —
 * which is exactly why the server has to be the one saying no.
 */

import type { APIRequestContext, APIResponse } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import {
  givePortalInvitation,
  givePortalSession,
  openPortalSection,
  signInToPortal,
} from "../fixtures/portal"
import {
  clinicianOpeningsBetween,
  diaryFor,
  diaryOnceItHolds,
  givePortalPatient,
  giveSchedulingPolicy,
  giveSelfBookableType,
  giveWorkingHoursAllWeek,
  type SchedulingPolicy,
  type SelfBookableType,
  walkToAnOpening,
} from "../fixtures/portal-appointments"
import { BACKEND_URL } from "../fixtures/stack"

const HOUR = 60 * 60 * 1000
const BOOKING = "/api/patient/booking"

interface PatientAppointment {
  id: string
  start_at: string
  status: string
}

/** A portal session that can change the diary: redeemed with its texted code. */
async function givePatientSession(
  api: ApiClient,
  request: APIRequestContext,
): Promise<{ patientId: string; headers: Record<string, string> }> {
  const { patient, email, phone } = await givePortalPatient(api)
  const token = await givePortalSession(api, request, patient.id, email, phone)
  return { patientId: patient.id, headers: { Authorization: `Bearer ${token}` } }
}

/** The code a booking route refuses with, under FastAPI's `detail` envelope. */
async function refusalCode(response: APIResponse): Promise<string> {
  return (await response.json()).detail.error.code
}

/**
 * The first opening the clinician's engine offers at least `hours` from now.
 *
 * Two days of slack on the far side so a run that starts late in the
 * clinician's working day still finds one.
 */
async function firstOpeningAfter(api: ApiClient, hours: number): Promise<string> {
  const from = new Date(Date.now() + hours * HOUR)
  const [first] = await clinicianOpeningsBetween(api, from, new Date(from.getTime() + 72 * HOUR))
  expect(first, `the diary has an opening ${hours}h or more out`).toBeTruthy()
  return first
}

function bookAs(
  request: APIRequestContext,
  headers: Record<string, string>,
  type: SelfBookableType,
  startAt: string,
): Promise<APIResponse> {
  return request.post(`${BACKEND_URL}${BOOKING}`, {
    headers,
    data: { start_at: startAt, session_type: type.name },
  })
}

async function ownAppointments(
  request: APIRequestContext,
  headers: Record<string, string>,
): Promise<PatientAppointment[]> {
  const listed = await request.get(`${BACKEND_URL}/api/patient/appointments`, { headers })
  expect(listed.status()).toBe(200)
  return (await listed.json()).data
}

test.describe("portal self-booking limits", () => {
  // Hours and policy live on the worker's practice, so both are put back
  // whether or not the test passed.
  const cleanups: (() => Promise<void>)[] = []
  test.afterEach(async () => {
    while (cleanups.length > 0) await cleanups.pop()?.()
  })

  /** A practice open every day, with one self-bookable type and this policy. */
  async function giveBookablePractice(
    api: ApiClient,
    policy: Partial<SchedulingPolicy>,
  ): Promise<SelfBookableType> {
    cleanups.push((await giveWorkingHoursAllWeek(api)).cleanup)
    cleanups.push((await giveSchedulingPolicy(api, policy)).restore)
    return giveSelfBookableType(api, `Therapy session ${Date.now().toString(36)}`)
  }

  test("a request waits for the practice, and reads confirmed once it is accepted", async ({
    api,
    page,
  }) => {
    await giveBookablePractice(api, {
      self_book_existing: true,
      self_book_mode: "request",
      min_notice_hours: 24,
    })

    const { patient, email, phone } = await givePortalPatient(api)
    await signInToPortal(page, await givePortalInvitation(api, patient.id, email, phone))
    await openPortalSection(page, "appointments")

    // --- the patient asks for a time ----------------------------------------
    await page.getByTestId("portal-appointments-book").click()
    await expect(page.getByTestId("appointments-slot-picker")).toBeVisible()
    await walkToAnOpening(page)
    await page.getByTestId("appointments-slot").first().click()
    await page.getByTestId("appointments-confirm-submit").click()
    await expect(page.getByTestId("appointments-confirmation")).toBeVisible()
    await page.getByTestId("appointments-confirmation-done").click()

    // A request, not a booking: the portal says it is waiting, and the diary
    // agrees.
    await expect(page.getByTestId("appointments-row")).toHaveCount(1)
    await expect(page.getByTestId("appointments-row-pending")).toBeVisible()
    const [requested] = await diaryOnceItHolds(api, patient.id, 1)
    expect(requested.status).toBe("pending")

    // --- the clinician accepts it -------------------------------------------
    const accepted = await api.patch<{ status: string }>(`/api/appointments/${requested.id}`, {
      status: "confirmed",
    })
    expect(accepted.status).toBe("confirmed")

    // --- and the patient sees it confirmed ----------------------------------
    await page.reload()
    await expect(page.getByTestId("appointments-row")).toHaveCount(1)
    await expect(page.getByTestId("appointments-row-pending")).toBeHidden()
    await expect(page.getByTestId("appointments-row-status")).toHaveText(/confirmed/i)
  })

  test("a time inside the minimum notice is refused and creates nothing", async ({
    api,
    request,
  }) => {
    const notice = 72
    const type = await giveBookablePractice(api, {
      self_book_existing: true,
      min_notice_hours: notice,
    })
    const { patientId, headers } = await givePatientSession(api, request)

    // An hour the clinician's diary is open, but sooner than the practice
    // takes online bookings.
    const tooSoon = (
      await clinicianOpeningsBetween(
        api,
        new Date(Date.now() + 2 * HOUR),
        new Date(Date.now() + (notice - 1) * HOUR),
      )
    )[0]
    expect(tooSoon, "the diary is open inside the notice period").toBeTruthy()

    // The portal does not offer it ...
    const date = tooSoon.slice(0, 10)
    const offered = await request.get(`${BACKEND_URL}${BOOKING}/slots?date=${date}`, { headers })
    expect(offered.status()).toBe(200)
    const offeredStarts = ((await offered.json()).data as { start_at: string }[]).map((slot) =>
      new Date(slot.start_at).getTime(),
    )
    expect(offeredStarts).not.toContain(new Date(tooSoon).getTime())

    // ... and the server will not take it when asked directly.
    const refused = await bookAs(request, headers, type, tooSoon)
    expect(refused.status()).toBe(409)
    expect(await refusalCode(refused)).toBe("INSIDE_NOTICE_WINDOW")
    expect(await ownAppointments(request, headers)).toEqual([])
    expect(await diaryFor(api, patientId)).toEqual([])

    // The same patient, a time past the notice: it books, so the refusal was
    // about the notice and not about this patient or this type.
    const pastNotice = await firstOpeningAfter(api, notice + 1)
    const booked = await bookAs(request, headers, type, pastNotice)
    expect(booked.status()).toBe(201)
    const [only] = await diaryOnceItHolds(api, patientId, 1)
    expect(new Date(only.start_at).getTime()).toBe(new Date(pastNotice).getTime())
  })

  test("one patient cannot see or change another patient's appointment", async ({
    api,
    request,
  }) => {
    const type = await giveBookablePractice(api, {
      self_book_existing: true,
      self_book_mode: "request",
      min_notice_hours: 24,
    })
    const owner = await givePatientSession(api, request)
    const stranger = await givePatientSession(api, request)

    const [ownersTime, anotherTime] = await clinicianOpeningsBetween(
      api,
      new Date(Date.now() + 25 * HOUR),
      new Date(Date.now() + 97 * HOUR),
    )
    const booked = await bookAs(request, owner.headers, type, ownersTime)
    expect(booked.status()).toBe(201)
    const theirs = (await booked.json()) as PatientAppointment

    // Control first: the owner sees it, so what follows is about who is asking.
    expect((await ownAppointments(request, owner.headers)).map((a) => a.id)).toEqual([theirs.id])

    // The stranger's list does not carry it.
    expect((await ownAppointments(request, stranger.headers)).map((a) => a.id)).not.toContain(
      theirs.id,
    )

    // And neither change route will touch it. Both answer as if the id did
    // not exist, so the routes cannot be used to learn which ids do.
    const cancel = await request.post(`${BACKEND_URL}${BOOKING}/${theirs.id}/cancel`, {
      headers: stranger.headers,
      data: { acknowledge_late_change: true },
    })
    expect(cancel.status()).toBe(404)
    expect(await refusalCode(cancel)).toBe("NOT_FOUND")

    const move = await request.post(`${BACKEND_URL}${BOOKING}/${theirs.id}/reschedule`, {
      headers: stranger.headers,
      data: { start_at: anotherTime, acknowledge_late_change: true },
    })
    expect(move.status()).toBe(404)
    expect(await refusalCode(move)).toBe("NOT_FOUND")

    // The owner's appointment is exactly as it was, on both sides.
    const stillTheirs = await ownAppointments(request, owner.headers)
    expect(stillTheirs).toHaveLength(1)
    expect(stillTheirs[0]).toMatchObject({ id: theirs.id, status: theirs.status })
    expect(new Date(stillTheirs[0].start_at).getTime()).toBe(new Date(ownersTime).getTime())

    const diary = await diaryFor(api, owner.patientId)
    expect(diary.map((a) => [a.id, a.status])).toEqual([[theirs.id, "pending"]])
    expect(await diaryFor(api, stranger.patientId)).toEqual([])
  })

  test("with self-booking off for existing clients, booking is refused", async ({
    api,
    request,
  }) => {
    // Everything else a booking needs is in place — hours, a self-bookable
    // type, a time past the notice — so the switch is the only thing missing.
    const type = await giveBookablePractice(api, {
      self_book_existing: false,
      min_notice_hours: 24,
    })
    const { patientId, headers } = await givePatientSession(api, request)
    const opening = await firstOpeningAfter(api, 25)

    const slots = await request.get(`${BACKEND_URL}${BOOKING}/slots?date=${opening.slice(0, 10)}`, {
      headers,
    })
    expect(slots.status()).toBe(403)
    expect(await refusalCode(slots)).toBe("SELF_BOOKING_DISABLED")

    const refused = await bookAs(request, headers, type, opening)
    expect(refused.status()).toBe(403)
    expect(await refusalCode(refused)).toBe("SELF_BOOKING_DISABLED")

    expect(await ownAppointments(request, headers)).toEqual([])
    expect(await diaryFor(api, patientId)).toEqual([])
  })
})
