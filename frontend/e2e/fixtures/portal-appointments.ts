// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * "Given X" helpers for the portal's appointments module: the practice-side
 * setup a patient needs before there is anything to book.
 *
 * Signing in is NOT here. `portal.ts` owns the invite-and-redeem seam that
 * every portal spec shares, and a second copy of it would be a second thing
 * to keep in step with how invitations are delivered.
 */

import { expect, type Page } from "@playwright/test"
import type { ApiClient } from "./api"
import { givePortalContactDetails } from "./portal"
import { type AvailabilityRule, givePatient, giveWorkingHours, type Patient } from "./scenarios"

/**
 * The practice-wide switch for patients it already has.
 *
 * Off for every practice until somebody says otherwise, which is why a spec
 * that books has to turn it on and a spec that wants the closed state passes
 * `false`. Distinct from `self_book_new`, which governs a stranger booking a
 * first visit through a public link — everybody reaching the portal is
 * already a patient here, so that is a different question.
 */
export async function letExistingClientsSelfBook(
  api: ApiClient,
  allowed = true,
): Promise<void> {
  await api.patch("/api/scheduling/policy", { self_book_existing: allowed })
}

/**
 * Hours on every weekday, so a spec can walk forward to any day and find
 * openings rather than having to work out which weekday it is running on.
 *
 * One rule per weekday, because the engine accumulates rules and two
 * covering the same day would offer every slot twice.
 *
 * Returns a `cleanup` the caller must run. Rules live on the worker's
 * clinician, so hours left behind constrain every later spec in the worker:
 * a session booked minutes from now (portal-telehealth.spec.ts) is refused as
 * "outside working hours" whenever the suite runs outside 09:00-17:00.
 */
export async function giveWorkingHoursAllWeek(api: ApiClient): Promise<{ cleanup: () => Promise<void> }> {
  const rules: AvailabilityRule[] = []
  for (let day = 0; day < 7; day += 1) {
    rules.push(await giveWorkingHours(api, day))
  }
  return {
    cleanup: async () => {
      for (const rule of rules) await api.delete(`/api/availability/rules/${rule.id}`)
    },
  }
}

export interface SelfBookableType {
  id: string
  name: string
  duration_minutes: number
}

/**
 * An appointment type an existing patient may book for themselves.
 *
 * `self_bookable` is an allow-list rather than a deny-list: a practice that
 * flips the master switch without opting a type in has opened nothing, which
 * is the safe direction and the state the second spec here relies on.
 */
export async function giveSelfBookableType(
  api: ApiClient,
  name: string,
): Promise<SelfBookableType> {
  return api.post<SelfBookableType>("/api/appointment-types", {
    name,
    duration_minutes: 50,
    audience: "existing",
    self_bookable: true,
    offerable: true,
  })
}

export interface SchedulingPolicy {
  min_notice_hours: number
  max_horizon_days: number
  self_book_existing: boolean
  self_book_mode: "request" | "auto"
}

/**
 * Change part of the practice's scheduling policy, and hand back a `restore`
 * that puts the changed fields back as they were.
 *
 * The policy belongs to the worker's practice, so a notice period or booking
 * mode a spec leaves behind would decide what every later spec in the worker
 * is offered.
 */
export async function giveSchedulingPolicy(
  api: ApiClient,
  changes: Partial<SchedulingPolicy>,
): Promise<{ restore: () => Promise<void> }> {
  const before = await api.get<SchedulingPolicy>("/api/scheduling/policy")
  await api.patch("/api/scheduling/policy", changes)
  const original = Object.fromEntries(
    Object.keys(changes).map((field) => [field, before[field as keyof SchedulingPolicy]]),
  )
  return { restore: async () => void (await api.patch("/api/scheduling/policy", original)) }
}

/**
 * Openings the clinician's own engine offers between two instants, earliest
 * first — the diary's answer, before any patient-facing policy narrows it.
 *
 * A spec about a refusal books one of these, so the refusal is about the rule
 * it names and not about a time nobody would have offered anyway.
 */
export async function clinicianOpeningsBetween(
  api: ApiClient,
  from: Date,
  to: Date,
): Promise<string[]> {
  const openings: string[] = []
  // A day either side, because the engine answers for the clinician's local
  // date and the window is in instants.
  const day = new Date(from.getTime() - 24 * 60 * 60 * 1000)
  while (day.getTime() <= to.getTime() + 24 * 60 * 60 * 1000) {
    const date = day.toISOString().slice(0, 10)
    const free = await api.get<{ slots: { start: string }[] }>(
      `/api/availability/slots?date=${date}&duration=50`,
    )
    for (const slot of free.slots) {
      const at = new Date(slot.start).getTime()
      if (at >= from.getTime() && at <= to.getTime()) openings.push(slot.start)
    }
    day.setUTCDate(day.getUTCDate() + 1)
  }
  return [...new Set(openings)].sort((a, b) => new Date(a).getTime() - new Date(b).getTime())
}

/** A patient of this practice who can be invited into the portal. */
export async function givePortalPatient(api: ApiClient): Promise<{
  patient: Patient
  email: string
  phone: string
}> {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone, date_of_birth: "1988-04-02" })
  return { patient, email, phone }
}

export interface ClinicianAppointment {
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
export async function diaryFor(api: ApiClient, patientId: string): Promise<ClinicianAppointment[]> {
  const start = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString()
  const end = new Date(Date.now() + 90 * 24 * 60 * 60 * 1000).toISOString()
  const all = await api.get<{ data: ClinicianAppointment[] }>(
    `/api/appointments?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
  )
  return all.data.filter((appointment) => appointment.patient_id === patientId)
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
export async function diaryOnceItHolds(
  api: ApiClient,
  patientId: string,
  count: number,
): Promise<ClinicianAppointment[]> {
  await expect.poll(async () => (await diaryFor(api, patientId)).length).toBe(count)
  return diaryFor(api, patientId)
}

/** How far past the day the picker opens on the walk below will look. */
const WALK_LIMIT_DAYS = 10

/**
 * Walk the picker forward until a day offers an opening, and answer how many
 * days that took.
 *
 * Counting days to the practice's notice period instead would make the
 * journey depend on two things it is not about: a policy the practice is free
 * to change, and what time of day the run happens to start. Both have to be
 * right for a fixed number of clicks to land somewhere bookable, and when
 * they are not the failure arrives as an empty grid several steps later.
 *
 * Asking the picker keeps the assertion about the patient taking an opening
 * the engine really offered. `skipDays` walks past days the caller already
 * knows it does not want — the move below wants a day other than the one it
 * is giving up.
 */
export async function walkToAnOpening(page: Page, skipDays = 0): Promise<number> {
  for (let day = 0; day < skipDays; day += 1) {
    await page.getByTestId("appointments-slots-next").click()
  }

  for (let day = skipDays; day <= skipDays + WALK_LIMIT_DAYS; day += 1) {
    const openings = page.getByTestId("appointments-slot")
    // The day has answered when it says it is empty or draws a grid; until
    // then it is still loading and counting openings would count nothing.
    await expect(openings.first().or(page.getByTestId("appointments-slots-empty"))).toBeVisible()
    if ((await openings.count()) > 0) return day
    await page.getByTestId("appointments-slots-next").click()
  }

  throw new Error(
    `No opening on any day from ${skipDays} to ${skipDays + WALK_LIMIT_DAYS} out. ` +
      "The practice has hours on every day of the week and self-booking on, so " +
      "either the notice period now reaches further than this walk, or the " +
      "openings are being computed for a clinician other than the one seeded.",
  )
}
