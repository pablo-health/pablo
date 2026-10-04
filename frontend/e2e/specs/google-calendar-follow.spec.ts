// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Google Calendar, end to end against the stand-in: connecting through the
 * real setup page, bringing a practice over from the calendar, following
 * the calendar a clinician chooses, and surviving Google forgetting the
 * sync token.
 *
 * Every read of the calendar is triggered through the same pass the
 * schedule runs (`POST /api/calendar/sync`), so what is proved here is what
 * the background sync does, not a shortcut past it.
 */

import type { Page } from "@playwright/test"
import { test, expect } from "../fixtures/auth"
import { ApiClient, ApiError } from "../fixtures/api"
import { givePatient, markCalendarSetupComplete } from "../fixtures/scenarios"
import {
  SCOPE_APP_CALENDAR,
  SCOPE_FREEBUSY,
  SCOPE_READ_EVENTS,
  google,
  localDateTime,
  localWeekday,
  plusMinutes,
  toUtc,
  type EventTime,
} from "../fixtures/google"

const SETUP_PATH = "/dashboard/settings/calendar"
const SESSION_MINUTES = 50

interface Appointment {
  id: string
  patient_id: string
  status: string
  start_at: string
  google_sync_status: string | null
}

interface Question {
  key: string
  source: string
  source_identifier: string
  title: string
  sessions: number
  match: { suggested_patient_id: string | null; possible: { patient_id: string; display_name: string }[] }
}

let stamp = 0

/**
 * A connection that starts from nothing: the backend forgets the last
 * connection and the stand-in becomes a new account. A new account per
 * spec means a new main-calendar id, so a full read of it can never judge
 * the sessions an earlier spec booked from a calendar of the same name.
 */
async function freshGoogle(api: ApiClient): Promise<string> {
  // A disconnect also turns following off, so the wizard's checkbox starts
  // unchecked and a click on it turns following on.
  try {
    await api.delete("/api/google-calendar/disconnect")
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) throw error
  }
  const account = `clinician-${Date.now()}-${stamp++}@example.test`
  await google.reset(account)
  return account
}

/**
 * Hours on every weekday, so an imported series lands inside them whatever
 * day it falls on. Returns the rules this added, for taking away again.
 */
async function ensureWorkingHours(api: ApiClient): Promise<string[]> {
  const rules = await api.get<{ data: { params: { day_of_week?: number } }[] }>(
    "/api/availability/rules",
  )
  const covered = new Set(rules.data.map((rule) => rule.params.day_of_week))
  const added: string[] = []
  for (let day = 0; day < 7; day += 1) {
    if (covered.has(day)) continue
    const rule = await api.post<{ id: string }>("/api/availability/rules", {
      rule_type: "working_hours",
      params: { day_of_week: day, start: "08:00", end: "18:00" },
    })
    added.push(rule.id)
  }
  return added
}

/** A weekly series from `start`, for `count` weeks. */
async function seedWeekly(
  calendarId: string,
  summary: string,
  start: EventTime,
  count: number,
): Promise<string> {
  const seeded = await google.seed(calendarId, {
    summary,
    start,
    end: plusMinutes(start, SESSION_MINUTES),
    recurrence: [`RRULE:FREQ=WEEKLY;COUNT=${count}`],
  })
  return seeded.id
}

/**
 * A weekly series from `start` until the day `untilDaysAhead` days out,
 * the way Google writes a series that "ends on" a date.
 *
 * Used where the series is to be proposed by an import scan. A series that
 * "ends after N occurrences" (`COUNT`) is judged finished by the scan's
 * has-the-rule-run-out check whatever is still ahead of it, and so is never
 * preselected; that is a product bug, reproduced apart from this spec, not
 * something to write the spec around.
 */
async function seedWeeklyUntil(
  calendarId: string,
  summary: string,
  start: EventTime,
  untilDaysAhead: number,
): Promise<string> {
  // An UNTIL is written in UTC: the end of that day on the practice's clock.
  const until = toUtc(localDateTime(untilDaysAhead, "23:59"))
    .toISOString()
    .replace(/[-:]|\.\d{3}/g, "")
  const seeded = await google.seed(calendarId, {
    summary,
    start,
    end: plusMinutes(start, SESSION_MINUTES),
    recurrence: [`RRULE:FREQ=WEEKLY;UNTIL=${until}`],
  })
  return seeded.id
}

/**
 * Connect through the setup page, then grant reading events by looking at
 * the week — the two round trips to Google a clinician makes. Ends on the
 * clients step with the week read, and following the main calendar when
 * asked to. Returns how many events the scan left alone.
 */
async function connectThroughSetup(
  page: Page,
  { follow }: { follow: boolean },
): Promise<{ leftAlone: number }> {
  await page.goto(SETUP_PATH)
  await page.getByRole("button", { name: "Continue with Google" }).click()
  // Google (the stand-in) sends the browser back with a code; the page
  // exchanges it and lands on Sessions, where the choices can still change.
  await expect(page.getByText("Google Calendar is connected.")).toBeVisible()
  await expect(page.getByRole("heading", { name: "Choose a calendar" })).toBeVisible()
  expect(await google.grant()).toEqual([SCOPE_APP_CALENDAR, SCOPE_FREEBUSY].sort())
  await page.getByRole("button", { name: "Continue", exact: true }).click()

  // The first scan only asks for the grant; the one after Google sends the
  // browser back is the one that reads the week. The first one's body is
  // gone once the page leaves for Google, so an unreadable body is not it.
  const scanned = page.waitForResponse(async (response) => {
    if (!response.url().includes("/api/calendar/import/scan") || !response.ok()) return false
    try {
      return "left_alone" in ((await response.json()) as object)
    } catch {
      return false
    }
  })
  await page.getByRole("button", { name: "Scan calendar" }).click()
  // Reading events is a second grant, asked for only now, and added to
  // the first rather than replacing it. The legend's count of possible
  // sessions is what says the week was read: the grid itself shows busy
  // time from the moment the connection can answer for it, before any scan.
  const proposal = (await (await scanned).json()) as { left_alone: number }
  await expect(page.getByTestId("qualifying-count")).toBeVisible()
  expect(await google.grant()).toEqual(
    [SCOPE_APP_CALENDAR, SCOPE_FREEBUSY, SCOPE_READ_EVENTS].sort(),
  )

  if (follow) {
    // Nothing is followed yet (freshGoogle turned it off), and the page
    // knows it: the checkbox is rendered from a status read that waited
    // for sign-in, so this click turns following ON.
    const box = page.getByLabel("Keep importing new sessions from this calendar")
    await expect(box).not.toBeChecked()
    const followed = page.waitForResponse(
      (response) =>
        response.url().includes("/api/google-calendar/followed-calendar") && response.ok(),
    )
    await box.click()
    await followed
    await expect(box).toBeChecked()
  }
  return { leftAlone: proposal.left_alone }
}

async function readCalendarsNow(api: ApiClient): Promise<void> {
  await api.post("/api/calendar/sync")
}

async function questions(api: ApiClient): Promise<Question[]> {
  return (await api.get<{ questions: Question[] }>("/api/calendar/outside-sessions/questions"))
    .questions
}

/** Answer one question as a new client, the way the review does. */
async function answerAsNewClient(api: ApiClient, title: string): Promise<void> {
  const question = (await questions(api)).find((q) => q.title === title)
  if (!question) throw new Error(`no question for ${title}`)
  await api.post("/api/calendar/outside-sessions/answer", {
    answers: [
      {
        source: question.source,
        source_identifier: question.source_identifier,
        patient_id: null,
        new_client_name: title,
        not_a_client: false,
      },
    ],
  })
}

/**
 * Charts called `name` in full. The review fills in a new client's first and
 * last name when the title is plainly one, so "Casey Morgan" is charted as
 * Casey / Morgan; one answered through the API with the title alone keeps it
 * as the first name. Either reads as the same name.
 */
async function patientsNamed(api: ApiClient, name: string): Promise<{ id: string }[]> {
  const lastWord = name.split(" ").at(-1) ?? name
  const page = await api.get<{ data: { id: string; first_name: string; last_name: string }[] }>(
    `/api/patients?search=${encodeURIComponent(lastWord)}&page_size=100`,
  )
  return page.data.filter((p) => `${p.first_name} ${p.last_name}`.trim() === name)
}

async function patientNamed(api: ApiClient, name: string): Promise<string> {
  const [found] = await patientsNamed(api, name)
  if (!found) throw new Error(`no client named ${name}`)
  return found.id
}

/**
 * No chart under these names, so each is a new client again. Charts outlive
 * a run (only the diary is cleared between runs), and a chart bearing the
 * calendar's wording would otherwise be matched instead of made.
 */
async function forgetClients(api: ApiClient, names: string[]): Promise<void> {
  for (const name of names) {
    for (const patient of await patientsNamed(api, name)) {
      await api.request("DELETE", `/api/patients/${patient.id}`, {
        acknowledged_retention_obligation: true,
      })
    }
  }
}

async function upcomingFor(api: ApiClient, patientId: string): Promise<Appointment[]> {
  const year = 365 * 24 * 60 * 60 * 1000
  const from = new Date().toISOString()
  const until = new Date(Date.now() + year).toISOString()
  const diary = await api.get<{ data: Appointment[] }>(
    `/api/appointments?start=${encodeURIComponent(from)}&end=${encodeURIComponent(until)}`,
  )
  return diary.data.filter((a) => a.patient_id === patientId && a.status !== "cancelled")
}

/** A sync status that says the session was moved, removed or held rather than left alone. */
function flagged(status: string | null): boolean {
  return status === "external_change" || status === "removed_in_google" || status === "missing_in_google"
}

async function heldRemovals(api: ApiClient): Promise<number> {
  return (await api.get<{ count: number }>("/api/google-calendar/held-removals")).count
}

/**
 * The week view runs Sunday to Saturday, so a session tomorrow is in next
 * week's view when today is Saturday.
 */
async function showTomorrow(page: Page): Promise<void> {
  await page.goto("/dashboard/calendar")
  if (localWeekday(1) === 0) {
    await page.getByRole("button", { name: "Next", exact: true }).click()
  }
}

test.describe.configure({ mode: "serial" })

/** Every client these specs make, so the diary and the client list are put back after each. */
const CLIENTS = [
  "Jordan Rivera",
  "Casey Morgan",
  "Riley Chen",
  "Avery Kim",
  "Morgan Lee",
  "Sam Patel",
  "Dana Brooks",
  "Taylor Quinn",
]

let addedRules: string[] = []
/** Charts made under a name typed in the review, found by id rather than by CLIENTS. */
const madeHere: string[] = []

interface NamedPatient {
  id: string
  first_name: string
  last_name: string
  needs_name?: boolean
}

async function patientsWithLastName(api: ApiClient, lastName: string): Promise<NamedPatient[]> {
  const page = await api.get<{ data: NamedPatient[] }>(
    `/api/patients?search=${encodeURIComponent(lastName)}&page_size=100`,
  )
  return page.data.filter((p) => p.last_name === lastName)
}

async function forgetByLastName(api: ApiClient, lastName: string): Promise<void> {
  for (const patient of await patientsWithLastName(api, lastName)) madeHere.push(patient.id)
  await forgetMadeHere(api)
}

async function forgetMadeHere(api: ApiClient): Promise<void> {
  for (const id of madeHere.splice(0)) {
    for (const appointment of await upcomingFor(api, id)) {
      await api.delete(`/api/appointments/${appointment.id}`)
    }
    await api.request("DELETE", `/api/patients/${id}`, {
      acknowledged_retention_obligation: true,
    })
  }
}

test.afterEach(async ({ api }) => {
  await forgetMadeHere(api)
  // The worker's diary is shared with every later spec, which books into
  // whatever openings are left; the sessions and hours made here are not
  // theirs to find.
  for (const name of CLIENTS) {
    for (const patient of await patientsNamed(api, name)) {
      for (const appointment of await upcomingFor(api, patient.id)) {
        await api.delete(`/api/appointments/${appointment.id}`)
      }
    }
  }
  await forgetClients(api, CLIENTS)
  for (const ruleId of addedRules) {
    await api.delete(`/api/availability/rules/${ruleId}`)
  }
  addedRules = []
})

test.beforeEach(async ({ api }) => {
  addedRules = await ensureWorkingHours(api)
  // The calendar page shows the setup wizard until this preference is set,
  // and only its own copy of the wizard sets it. The one on the Settings
  // page, which these specs walk, does not — so "View calendar" after
  // an import there lands on the wizard's first step, with the sessions
  // just imported out of sight. A product bug, reported apart from this
  // spec; the suite's convention is to settle the preference up front.
  await markCalendarSetupComplete(api)
})

test("a practice is brought over from the calendar through the setup page", async ({
  signedInPage: page,
  api,
}) => {
  await freshGoogle(api)
  await forgetClients(api, ["Jordan Rivera"])
  // Eight weeks behind and eight ahead, so the rhythm is visible and the
  // next session is tomorrow.
  await seedWeeklyUntil("primary", "Jordan Rivera", localDateTime(1 - 8 * 7, "10:00"), 1 + 7 * 7)
  // A one-off is not a client and must be left alone.
  await google.seed("primary", {
    summary: "Dentist",
    start: localDateTime(2, "15:00"),
    end: plusMinutes(localDateTime(2, "15:00"), 30),
  })

  const { leftAlone } = await connectThroughSetup(page, { follow: false })
  // The one-off didn't fit the pattern. (The grid's own count is of weekday
  // cells, so it says nothing on a weekend.)
  expect(leftAlone).toBe(1)

  await page.getByRole("button", { name: "Continue", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Which of these are clients?" })).toBeVisible()
  await expect(page.getByRole("checkbox", { name: "Jordan Rivera" })).toBeChecked()
  await page.getByRole("button", { name: "Add 1 client", exact: true }).click()
  await expect(page.getByRole("heading", { name: "1 client added" })).toBeVisible()
  await expect(page.getByText("8 upcoming appointments added.")).toBeVisible()

  await page.getByRole("button", { name: "View calendar" }).click()
  await page.waitForURL(/\/dashboard\/calendar/)
  if (localWeekday(1) === 0) {
    await page.getByRole("button", { name: "Next", exact: true }).click()
  }
  await expect(page.getByText("Jordan Rivera", { exact: true })).toBeVisible()

  const patientId = await patientNamed(api, "Jordan Rivera")
  expect(await upcomingFor(api, patientId)).toHaveLength(8)
})

test("following the main calendar asks once per client and then books on its own", async ({
  signedInPage: page,
  api,
}) => {
  await freshGoogle(api)
  await forgetClients(api, ["Casey Morgan", "Riley Chen"])
  await connectThroughSetup(page, { follow: true })

  const seriesId = await seedWeekly("primary", "Casey Morgan", localDateTime(1, "11:00"), 3)
  await readCalendarsNow(api)

  await showTomorrow(page)
  await expect(page.getByText("1 session from your Google Calendar needs a client")).toBeVisible()
  await page.getByRole("button", { name: "Review", exact: true }).click()
  const review = page.getByRole("dialog")
  await expect(review.getByRole("heading", { name: "Which of these are clients?" })).toBeVisible()
  const row = review.getByRole("checkbox", { name: "Casey Morgan" })
  await expect(row).not.toBeChecked()
  await row.click()
  await review.getByRole("button", { name: "Save", exact: true }).click()
  await expect(review).toBeHidden()
  await expect(page.getByText("Casey Morgan", { exact: true })).toBeVisible()

  const patientId = await patientNamed(api, "Casey Morgan")
  expect(await upcomingFor(api, patientId)).toHaveLength(3)

  // The other service extends the series: the next session books without asking.
  await google.change("primary", seriesId, { recurrence: ["RRULE:FREQ=WEEKLY;COUNT=4"] })
  await readCalendarsNow(api)
  expect(await questions(api)).toHaveLength(0)
  expect(await upcomingFor(api, patientId)).toHaveLength(4)

  // The series is retitled and extended again: the same series id no
  // longer proves the same client, so the new session is asked about,
  // with the remembered client offered.
  await google.change("primary", seriesId, {
    summary: "Riley Chen",
    recurrence: ["RRULE:FREQ=WEEKLY;COUNT=5"],
  })
  await readCalendarsNow(api)
  const [asked] = await questions(api)
  expect(asked.title).toBe("Riley Chen")
  expect(asked.sessions).toBe(1)
  expect(asked.match.suggested_patient_id).toBe(patientId)
  expect(await upcomingFor(api, patientId)).toHaveLength(4)

  await page.reload()
  await expect(page.getByText("1 session from your Google Calendar needs a client")).toBeVisible()
  await page.getByRole("button", { name: "Review", exact: true }).click()
  const again = page.getByRole("dialog")
  await expect(again.getByRole("checkbox", { name: "Riley Chen" })).toBeChecked()
  await expect(again.getByRole("combobox", { name: "Which client is Riley Chen?" })).toHaveValue(
    patientId,
  )
})

test("choosing another calendar reads its sessions and leaves the main calendar's alone", async ({
  signedInPage: page,
  api,
}) => {
  const account = await freshGoogle(api)
  await forgetClients(api, ["Avery Kim", "Morgan Lee"])
  await connectThroughSetup(page, { follow: true })

  await seedWeekly("primary", "Avery Kim", localDateTime(1, "12:00"), 3)
  await readCalendarsNow(api)
  await answerAsNewClient(api, "Avery Kim")
  const averyId = await patientNamed(api, "Avery Kim")
  expect(await upcomingFor(api, averyId)).toHaveLength(3)

  const practice = await google.addCalendar("Practice")
  await seedWeekly(practice.id, "Morgan Lee", localDateTime(1, "13:00"), 3)

  await page.goto("/dashboard/settings/calendars")
  await expect(page.getByLabel("Keep importing new sessions")).toBeChecked()
  const picker = page.getByRole("combobox", { name: "Import sessions from" })
  // Main first, and the second calendar offered. (Today the list also
  // carries the calendar Pablo made for its own sessions, as Google's
  // calendar list does; whether to offer that one is the product's call,
  // so it is not pinned here.)
  await expect(picker.locator("option").first()).toHaveText(account)
  await expect(picker.locator("option", { hasText: "Practice" })).toHaveCount(1)
  await expect(page.getByTestId("followed-calendar-line")).toContainText(
    `Pablo reads the events on ${account}`,
  )
  const chosen = page.waitForResponse(
    (response) =>
      response.url().includes("/api/google-calendar/followed-calendar") && response.ok(),
  )
  await picker.selectOption("Practice")
  await chosen
  await expect(page.getByTestId("followed-calendar-line")).toContainText(
    "Pablo reads the events on Practice",
  )

  await readCalendarsNow(api)
  const asked = await questions(api)
  expect(asked.map((q) => q.title)).toEqual(["Morgan Lee"])
  await answerAsNewClient(api, "Morgan Lee")
  const morganId = await patientNamed(api, "Morgan Lee")
  expect(await upcomingFor(api, morganId)).toHaveLength(3)

  // The main calendar's sessions were booked from a calendar no longer
  // read; nothing about them changed.
  const avery = await upcomingFor(api, averyId)
  expect(avery).toHaveLength(3)
  expect(avery.map((a) => a.google_sync_status).filter(flagged)).toEqual([])
  expect(await heldRemovals(api)).toBe(0)
  expect(await questions(api)).toHaveLength(0)
})

test("an expired sync token is read over from the start without losing a session", async ({
  signedInPage: page,
  api,
}) => {
  await freshGoogle(api)
  await forgetClients(api, ["Sam Patel"])
  await connectThroughSetup(page, { follow: true })

  await seedWeekly("primary", "Sam Patel", localDateTime(1, "14:00"), 4)
  await readCalendarsNow(api)
  await answerAsNewClient(api, "Sam Patel")
  const samId = await patientNamed(api, "Sam Patel")
  const booked = await upcomingFor(api, samId)
  expect(booked).toHaveLength(4)

  // A read with nothing new, resumed from the token the first read stored.
  await readCalendarsNow(api)
  const seenBefore = (await google.requests()).length

  await google.expireSyncTokens("primary")
  await readCalendarsNow(api)

  // Google refused the token, and the read of the followed calendar started
  // over rather than failing. (The pass also reads the calendar Pablo
  // writes to, a calendar Google made for the app; that one is left out.)
  const reads = (await google.requests())
    .slice(seenBefore)
    .filter(
      (r) =>
        r.method === "GET" && r.path.endsWith("/events") && !r.path.includes("group.calendar"),
    )
  expect(reads.map((r) => r.status)).toEqual([410, 200])

  const after = await upcomingFor(api, samId)
  expect(after.map((a) => a.id).sort()).toEqual(booked.map((a) => a.id).sort())
  expect(after.map((a) => a.status)).toEqual(["confirmed", "confirmed", "confirmed", "confirmed"])
  // Nothing moved, cancelled, flagged or held; an unchanged followed event
  // settles to "synced", which is not a flag.
  expect(after.map((a) => a.google_sync_status).filter(flagged)).toEqual([])
  expect(await heldRemovals(api)).toBe(0)
  expect(await questions(api)).toHaveLength(0)

  // And the next read resumes from the new token without asking again.
  await readCalendarsNow(api)
  expect(await questions(api)).toHaveLength(0)
  expect(await upcomingFor(api, samId)).toHaveLength(4)

  // Google's access tokens last an hour. When one is aged out the next call
  // is refused, the client refreshes with the refresh token it was granted
  // for offline access, and the read goes on; nothing about the sessions
  // changes.
  const beforeRefresh = (await google.requests()).length
  await google.expireAccessTokens()
  await readCalendarsNow(api)
  const refreshed = (await google.requests()).slice(beforeRefresh)
  expect(refreshed.some((r) => r.status === 401)).toBe(true)
  expect(
    refreshed.some((r) => r.method === "POST" && r.path === "/token" && r.status === 200),
  ).toBe(true)
  expect(refreshed.at(-1)?.status).toBe(200)
  expect(await questions(api)).toHaveLength(0)
  expect(await upcomingFor(api, samId)).toHaveLength(4)
})

test("a session titled with a client's initials is offered that client", async ({
  signedInPage: page,
  api,
}) => {
  await freshGoogle(api)
  await forgetClients(api, ["Taylor Quinn"])
  const taylor = await givePatient(api, { first_name: "Taylor", last_name: "Quinn" })
  await connectThroughSetup(page, { follow: true })
  await seedWeekly("primary", "T.Q.", localDateTime(1, "15:00"), 2)
  await readCalendarsNow(api)

  // Asked, not booked: initials are only ever a suggestion.
  expect(await upcomingFor(api, taylor.id)).toHaveLength(0)
  await showTomorrow(page)
  await page.getByRole("button", { name: "Review", exact: true }).click()
  const review = page.getByRole("dialog")
  await expect(review.getByRole("checkbox", { name: "T.Q." })).toBeChecked()
  await expect(review.getByRole("combobox", { name: "Which client is T.Q.?" })).toHaveValue(
    taylor.id,
  )
})

test("a new client from an initials event is named as typed", async ({
  signedInPage: page,
  api,
}) => {
  await freshGoogle(api)
  await forgetByLastName(api, "Vance")
  await connectThroughSetup(page, { follow: true })
  // One session, so the initials are one question rather than one per event.
  await seedWeekly("primary", "Z.V.", localDateTime(1, "16:00"), 1)
  await readCalendarsNow(api)

  await showTomorrow(page)
  await page.getByRole("button", { name: "Review", exact: true }).click()
  const review = page.getByRole("dialog")
  await expect(review.getByText("New client", { exact: true })).toBeVisible()
  // Initials say nothing about the name: both fields start empty, with the
  // calendar's wording beside them.
  const [question] = (await questions(api)).filter((q) => q.title === "Z.V.")
  const fields = review.getByTestId(`new-client-name-${question.key}`)
  const first = fields.getByLabel("First name")
  const last = fields.getByLabel("Last name")
  await expect(first).toHaveValue("")
  await expect(last).toHaveValue("")
  await expect(fields.getByText("On the calendar: Z.V.")).toBeVisible()

  await first.fill("Zara")
  await last.fill("Vance")
  await expect(review.getByRole("checkbox", { name: "Z.V." })).toBeChecked()
  await review.screenshot({ path: test.info().outputPath("new-client-name-row.png") })
  await review.getByRole("button", { name: "Save", exact: true }).click()
  await expect(review).toBeHidden()

  const [zara] = await patientsWithLastName(api, "Vance")
  expect(zara).toMatchObject({ first_name: "Zara", last_name: "Vance", needs_name: false })
  madeHere.push(zara.id)
  expect(await upcomingFor(api, zara.id)).toHaveLength(1)

  await page.goto(`/dashboard/patients/${zara.id}`)
  await expect(page.getByRole("heading", { name: "Zara Vance" })).toBeVisible()
  await expect(page.getByTestId("needs-name")).toHaveCount(0)
})

test("disconnecting takes Pablo off the account and forgets what it read, keeping the sessions", async ({
  signedInPage: page,
  api,
}) => {
  await freshGoogle(api)
  await forgetClients(api, ["Dana Brooks"])
  await connectThroughSetup(page, { follow: true })
  await seedWeekly("primary", "Dana Brooks", localDateTime(1, "13:00"), 3)
  await readCalendarsNow(api)
  await answerAsNewClient(api, "Dana Brooks")
  const danaId = await patientNamed(api, "Dana Brooks")
  const booked = await upcomingFor(api, danaId)
  expect(booked).toHaveLength(3)
  const before = (await google.requests()).length

  await page.goto("/dashboard/settings/calendars")
  await page.getByRole("button", { name: "Disconnect", exact: true }).click()
  const confirm = page.getByRole("dialog", { name: "Disconnect Google Calendar?" })
  await expect(
    confirm.getByText("Pablo stops using your Google Calendar and deletes what it read from it.", {
      exact: false,
    }),
  ).toBeVisible()
  await confirm.getByRole("button", { name: "Disconnect", exact: true }).click()
  await expect(page.getByText("Not connected.")).toBeVisible()

  // Google was asked to withdraw the grant, and did: the account no longer
  // lists Pablo.
  const revokes = (await google.requests())
    .slice(before)
    .filter((r) => r.method === "POST" && r.path === "/revoke")
  expect(revokes.map((r) => r.status)).toEqual([200])
  expect(await google.grant()).toEqual([])

  // The sessions booked in Pablo stay.
  expect((await upcomingFor(api, danaId)).map((a) => a.id).sort()).toEqual(
    booked.map((a) => a.id).sort(),
  )

  // Connecting again starts from the grant it asks for, not the old one,
  // with following off until it is turned on again (the helper checks the
  // box starts unchecked). The answer about who the series is was forgotten
  // with the rest of what was read: the series is asked about again.
  await connectThroughSetup(page, { follow: true })
  await readCalendarsNow(api)
  const [asked] = (await questions(api)).filter((q) => q.title === "Dana Brooks")
  expect(asked).toBeDefined()

  // Answering it again books nothing new: the sessions kept their link to
  // the events, so the same appointments are picked back up.
  await api.post("/api/calendar/outside-sessions/answer", {
    answers: [
      {
        source: asked.source,
        source_identifier: asked.source_identifier,
        patient_id: danaId,
        new_client_name: null,
        not_a_client: false,
      },
    ],
  })
  expect(await questions(api)).toHaveLength(0)
  expect((await upcomingFor(api, danaId)).map((a) => a.id).sort()).toEqual(
    booked.map((a) => a.id).sort(),
  )
})
