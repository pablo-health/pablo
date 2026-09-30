// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A calendar feed a clinician follows, from the URL pasted into Settings to
 * the sessions on the calendar.
 *
 * The feed is the two captured SimplePractice reads under
 * backend/tests/fixtures/simplepractice_feed, served by scripts/fake_ical.py
 * with every event moved forward to start after today. One account, one set
 * of appointments and UIDs, read once with the calendar-sync setting on
 * initials ("J.A. Appointment") and once on full names ("John Adams
 * Appointment"). The backend reads the feed from the fake through
 * ICAL_FEED_BASE_URL; the URL typed here still has to pass the provider's
 * allowlist, which is why it names SimplePractice's own host.
 *
 * What is proved, each through the real app:
 *
 * - Initials never identify a client, so an initials feed books nothing and
 *   asks about every session, one at a time; an answer books that one
 *   session and pre-fills the next.
 * - Settings say when a feed shows initials, and stop once it shows names.
 * - A full name books without asking when exactly one chart bears it; a
 *   name with no chart is one question for the client's whole run; a name
 *   two charts share is asked about every time.
 * - A session for an inactive client is asked, not booked, with the offer to
 *   make them active again; confirming books it and does.
 *
 * All of it in one practice of its own (fixtures/freshPractice.ts), because a
 * feed puts a season of sessions on the calendar and a question on each. The
 * tests run in file order and build on each other's reads, so a failure stops
 * the file rather than retrying against a practice that has already answered.
 */

import { expect, test } from "../fixtures/auth"
import type { Locator, Page } from "@playwright/test"
import type { ApiClient } from "../fixtures/api"
import { signInToFreshPractice, type FreshPractice } from "../fixtures/freshPractice"
import {
  givePatient,
  giveWorkingHours,
  markCalendarSetupComplete,
  type Patient,
} from "../fixtures/scenarios"

test.describe.configure({ mode: "serial", retries: 0 })

/**
 * The practice's timezone, pinned on the browser for the reason
 * scheduling.spec.ts gives: the runner's clock and the practice's can name
 * different days, and every date here is read off the practice's.
 *
 * Must match the clinician default in `app.models.user`.
 */
const PRACTICE_TIMEZONE = "America/New_York"

/** The feed URLs a clinician pastes. Both pass the allowlist as typed; the
 * stack reads them from the fake, which serves each capture by its name. */
const INITIALS_FEED = "https://secure.simplepractice.com/ical/initials.ics"
const FULL_NAMES_FEED = "https://secure.simplepractice.com/ical/full-names.ics"

/** What the captures hold, per client, so a count here is a count there. */
const FEED = {
  /** Four clients arrive as "J.A." on the initials read. */
  initialsEvents: 38,
  /** John Adams: three sessions, then James Anderson takes the Tuesday slot. */
  johnAdamsEvents: 3,
  jamesAndersonEvents: 30,
  pabloBearEvents: 6,
}

interface Connection {
  ehr_system: string
  connected: boolean
  last_synced_at: string | null
  title_style: string | null
}

interface FeedRead {
  created: number
  updated: number
  deleted: number
  unchanged: number
  unmatched_events: { ical_uid: string; client_identifier: string }[]
  errors: string[]
}

interface Question {
  key: string
  title: string
  sessions: number
  next_start_at: string
  outside_session_id: string | null
  client_inactive: boolean
  match: {
    patient: { patient_id: string } | null
    possible: { patient_id: string; display_name: string }[]
    suggested_patient_id: string | null
  }
}

interface Appointment {
  id: string
  patient_id: string
  start_at: string
  status: string
}

async function connections(api: ApiClient): Promise<Connection[]> {
  return (await api.get<{ connections: Connection[] }>("/api/ical-sync/status")).connections
}

/** Follow `feedUrl` and nothing else, the way Settings would, through the API. */
async function followFeed(api: ApiClient, feedUrl: string): Promise<void> {
  for (const connection of await connections(api)) {
    await api.delete(`/api/ical-sync/${connection.ehr_system}`)
  }
  await api.post("/api/ical-sync/configure", { ehr_system: "simplepractice", feed_url: feedUrl })
}

/** One read of the followed feed: what the sync does on its own. */
async function readFeed(api: ApiClient): Promise<FeedRead> {
  const [read] = await api.post<FeedRead[]>("/api/ical-sync/sync")
  expect(read.errors).toEqual([])
  return read
}

async function questions(api: ApiClient): Promise<Question[]> {
  return (await api.get<{ questions: Question[] }>("/api/calendar/outside-sessions/questions"))
    .questions
}

/** Every appointment on the books, a year either side of now, cancelled ones aside. */
async function booked(api: ApiClient): Promise<Appointment[]> {
  const year = 365 * 24 * 60 * 60 * 1000
  const from = new Date(Date.now() - year).toISOString()
  const until = new Date(Date.now() + year).toISOString()
  const list = await api.get<{ data: Appointment[] }>(
    `/api/appointments?start=${encodeURIComponent(from)}&end=${encodeURIComponent(until)}`,
  )
  return list.data.filter((appointment) => appointment.status !== "cancelled")
}

async function patientStatus(api: ApiClient, patientId: string): Promise<string> {
  return (await api.get<Patient>(`/api/patients/${patientId}`)).status
}

/** The practice-local Sunday that starts the week `instant` falls in, as a
 * day count, so two of them subtract to whole weeks. */
function weekOf(instant: Date): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: PRACTICE_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
  }).formatToParts(instant)
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? ""
  const weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(part("weekday"))
  const sunday = Date.UTC(Number(part("year")), Number(part("month")) - 1, Number(part("day")) - weekday)
  return Math.round(sunday / (24 * 60 * 60 * 1000))
}

/**
 * The calendar, on the week `instant` falls in. The week view opens on this
 * week and runs Sunday to Saturday; the feed's first session is anywhere in
 * the next fortnight, so the spec steps forward rather than assuming.
 */
async function openCalendarOn(page: Page, instant: Date): Promise<void> {
  await page.goto("/dashboard/calendar")
  const weeksAhead = (weekOf(instant) - weekOf(new Date())) / 7
  for (let step = 0; step < weeksAhead; step += 1) {
    await page.getByRole("button", { name: "Next", exact: true }).click()
  }
}

/** The "Which of these are clients?" review, opened from the calendar's banner. */
async function openReview(page: Page): Promise<Locator> {
  await page.getByTestId("outside-sessions-line").getByRole("button", { name: "Review" }).click()
  const dialog = page.getByRole("dialog", { name: "Which of these are clients?" })
  await expect(dialog).toBeVisible()
  return dialog
}

/**
 * Signed into the feed practice, with its calendar past first-run: the
 * calendar page opens on the hours wizard until the practice keeps some.
 */
async function signIn(browser: Parameters<typeof signInToFreshPractice>[0]): Promise<FreshPractice> {
  const practice = await signInToFreshPractice(browser, "feed", { timezoneId: PRACTICE_TIMEZONE })
  await markCalendarSetupComplete(practice.api)
  const rules = await practice.api.get<{ data: unknown[] }>("/api/availability/rules")
  if (rules.data.length === 0) {
    // Monday, once: rules accumulate, and the wizard asks for any at all.
    await giveWorkingHours(practice.api, 0)
  }
  return practice
}

test("an initials feed books nothing and asks about each session, one at a time", async ({
  browser,
}) => {
  const { page, context, api } = await signIn(browser)
  try {
    // Two charts the feed's "J.A." could mean. Neither name is on the
    // full-name read, so later tests meet them only as answers already given.
    const jordan = await givePatient(api, { first_name: "Jordan", last_name: "Avery" })
    await givePatient(api, { first_name: "Jesse", last_name: "Archer" })

    // Connect the feed through Settings, the way a clinician does.
    await page.goto("/dashboard/settings/calendars")
    const card = page.getByRole("region", { name: "EHR calendars" })
    await card.getByLabel("EHR System").selectOption("simplepractice")
    await card.getByLabel("iCal Feed URL").fill(INITIALS_FEED)
    await card.getByRole("button", { name: "Connect", exact: true }).click()
    await expect(card.getByText(/Connected! Found \d+ appointments/)).toBeVisible()
    await expect(card.getByText("SimplePractice", { exact: true })).toBeVisible()

    // The first read: nothing is booked, because initials are not an identity.
    const before = await booked(api)
    const read = await readFeed(api)
    expect(read.created).toBe(0)
    expect(await booked(api)).toHaveLength(before.length)

    // Every J.A. session is its own question, none of them answered yet.
    const asked = (await questions(api)).filter((q) => q.title === "J.A. Appointment")
    expect(asked).toHaveLength(FEED.initialsEvents)
    for (const question of asked) {
      expect(question.outside_session_id).toBeTruthy()
      expect(question.sessions).toBe(1)
      expect(question.match.suggested_patient_id).toBeNull()
    }

    // The calendar shows them as "who is this?" blocks, not appointments.
    await openCalendarOn(page, new Date(asked[0].next_start_at))
    const blocks = page.getByTestId("outside-session").filter({ hasText: "J.A. Appointment" })
    await expect(blocks.first()).toBeVisible()
    await expect(blocks.first()).toContainText("Who is this?")
    await expect(page.getByTestId("outside-sessions-line")).toContainText("need a client")

    // The review offers each session as a question of its own, with the two
    // charts to choose from and nothing preselected.
    let review = await openReview(page)
    const rows = review.getByRole("checkbox", { name: "J.A. Appointment" })
    await expect(rows).toHaveCount(FEED.initialsEvents)
    await expect(review.getByRole("checkbox", { name: "J.A. Appointment", checked: true })).toHaveCount(0)
    const pickers = review.getByRole("combobox", { name: "Which client is J.A. Appointment?" })
    await expect(pickers).toHaveCount(FEED.initialsEvents)

    // Answer the soonest one with one client.
    await rows.first().click()
    await pickers.first().selectOption(jordan.id)
    const answered = page.waitForResponse(
      (response) =>
        response.url().includes("/api/calendar/outside-sessions/answer") && response.ok(),
    )
    await review.getByRole("button", { name: "Save", exact: true }).click()
    await answered
    await expect(review).toBeHidden()

    // Exactly that one session is booked, to that client.
    const after = await booked(api)
    expect(after).toHaveLength(before.length + 1)
    const added = after.find((a) => !before.some((b) => b.id === a.id))
    expect(added?.patient_id).toBe(jordan.id)
    expect(added?.start_at).toBe(asked[0].next_start_at)

    // The next J.A. session is pre-filled with that client, and still asked.
    const remaining = (await questions(api)).filter((q) => q.title === "J.A. Appointment")
    expect(remaining).toHaveLength(FEED.initialsEvents - 1)
    expect(remaining[0].next_start_at).toBe(asked[1].next_start_at)
    expect(remaining[0].match.suggested_patient_id).toBe(jordan.id)

    review = await openReview(page)
    await expect(review.getByRole("checkbox", { name: "J.A. Appointment" })).toHaveCount(
      FEED.initialsEvents - 1,
    )
    await expect(review.getByRole("checkbox", { name: "J.A. Appointment" }).first()).toBeChecked()
    await expect(
      review.getByRole("combobox", { name: "Which client is J.A. Appointment?" }).first(),
    ).toHaveValue(jordan.id)
  } finally {
    await context.close()
  }
})

test("settings say when a feed shows initials, and stop once it shows names", async ({
  browser,
}) => {
  const { page, context, api } = await signIn(browser)
  try {
    // The initials feed, read: the previous test's state, made sure of.
    const [connection] = await connections(api)
    expect(connection).toMatchObject({ ehr_system: "simplepractice", title_style: "initials" })

    const note =
      "This feed shows clients by their initials, so Pablo asks about every session. Showing full names in the calendar sync means fewer questions."
    await page.goto("/dashboard/settings/calendars")
    await expect(page.getByTestId("feed-initials-note")).toHaveText(note)

    // Switch to the full-name feed through Settings.
    await page.getByRole("button", { name: "Disconnect SimplePractice" }).click()
    await expect(page.getByTestId("feed-initials-note")).toHaveCount(0)
    await page.getByLabel("iCal Feed URL").fill(FULL_NAMES_FEED)
    await page.getByRole("button", { name: "Connect", exact: true }).click()
    await expect(page.getByText(/Connected! Found \d+ appointments/)).toBeVisible()

    // A read of the full-name feed, and the note is gone for good.
    await readFeed(api)
    expect((await connections(api))[0].title_style).toBe("names")
    await page.reload()
    await expect(page.getByText("SimplePractice", { exact: true })).toBeVisible()
    await expect(page.getByTestId("feed-initials-note")).toHaveCount(0)
  } finally {
    await context.close()
  }
})

test("a full name books the one chart that bears it, and a stranger is one question", async ({
  browser,
}) => {
  const { page, context, api } = await signIn(browser)
  try {
    await followFeed(api, FULL_NAMES_FEED)
    const john = await givePatient(api, { first_name: "John", last_name: "Adams" })

    const read = await readFeed(api)
    expect(read.created).toBe(FEED.johnAdamsEvents)

    // John's sessions are booked without asking, and on the calendar.
    const johns = (await booked(api)).filter((a) => a.patient_id === john.id)
    expect(johns).toHaveLength(FEED.johnAdamsEvents)
    expect((await questions(api)).some((q) => q.title === "John Adams Appointment")).toBe(false)
    const soonest = johns.map((a) => a.start_at).sort()[0]
    await openCalendarOn(page, new Date(soonest))
    await expect(page.getByText("John Adams", { exact: true }).first()).toBeVisible()

    // James Anderson, with no chart, is one question for his whole run.
    const james = (await questions(api)).filter((q) => q.title === "James Anderson Appointment")
    expect(james).toHaveLength(1)
    expect(james[0].sessions).toBe(FEED.jamesAndersonEvents)
    expect(james[0].outside_session_id).toBeNull()
    const review = await openReview(page)
    const row = review.getByRole("checkbox", { name: "James Anderson Appointment" })
    await expect(row).toHaveCount(1)
    await expect(row).not.toBeChecked()
    await expect(review.getByText(`${FEED.jamesAndersonEvents} sessions`)).toBeVisible()
  } finally {
    await context.close()
  }
})

test("a name two charts share is asked about every time", async ({ browser }) => {
  const { page, context, api } = await signIn(browser)
  try {
    const pablo = await givePatient(api, { first_name: "Pablo", last_name: "Bear" })
    const pabloA = await givePatient(api, { first_name: "Pablo A", last_name: "Bear" })

    const read = await readFeed(api)
    expect(read.created).toBe(0)
    const bears = (await booked(api)).filter((a) => [pablo.id, pabloA.id].includes(a.patient_id))
    expect(bears).toHaveLength(0)

    // Each of Pablo Bear's sessions is its own question, both charts offered.
    const asked = (await questions(api)).filter((q) => q.title === "Pablo Bear Appointment")
    expect(asked).toHaveLength(FEED.pabloBearEvents)
    for (const question of asked) {
      expect(question.outside_session_id).toBeTruthy()
      expect(question.sessions).toBe(1)
      expect(question.match.possible.map((c) => c.patient_id).sort()).toEqual(
        [pablo.id, pabloA.id].sort(),
      )
    }
    await openCalendarOn(page, new Date(asked[0].next_start_at))
    const review = await openReview(page)
    await expect(review.getByRole("checkbox", { name: "Pablo Bear Appointment" })).toHaveCount(
      FEED.pabloBearEvents,
    )
    const picker = review.getByRole("combobox", { name: "Which client is Pablo Bear Appointment?" })
    await expect(picker).toHaveCount(FEED.pabloBearEvents)
    const choices = await picker.first().getByRole("option").allTextContents()
    expect(choices.sort()).toEqual(["New client", "Pablo A Bear", "Pablo Bear"])
  } finally {
    await context.close()
  }
})

test("a session for an inactive client is asked, and confirming brings them back", async ({
  browser,
}) => {
  const { page, context, api } = await signIn(browser)
  try {
    const jane = await givePatient(api, {
      first_name: "Jane",
      last_name: "Smith",
      status: "inactive",
    })

    const read = await readFeed(api)
    expect(read.created).toBe(0)
    expect((await booked(api)).filter((a) => a.patient_id === jane.id)).toHaveLength(0)

    // Asked, with her chart offered and the offer to make her active again.
    const [asked] = (await questions(api)).filter((q) => q.title === "jane smith Appointment")
    expect(asked).toMatchObject({ client_inactive: true, sessions: 1 })
    expect(asked.match.suggested_patient_id).toBe(jane.id)

    await openCalendarOn(page, new Date(asked.next_start_at))
    const review = await openReview(page)
    const row = review.getByRole("checkbox", { name: "jane smith Appointment" })
    await expect(row).toBeChecked()
    const reactivate = review.getByRole("checkbox", { name: "Make Jane Smith active again" })
    await expect(reactivate).toBeChecked()

    // Only her question is confirmed: the others stay for later.
    for (const other of await review.getByRole("checkbox", { checked: true }).all()) {
      const label = await other.getAttribute("aria-label")
      if (label !== "jane smith Appointment" && label !== "Make Jane Smith active again") {
        await other.click()
      }
    }
    const answered = page.waitForResponse(
      (response) =>
        response.url().includes("/api/calendar/outside-sessions/answer") && response.ok(),
    )
    await review.getByRole("button", { name: "Save", exact: true }).click()
    await answered
    await expect(review).toBeHidden()

    expect((await booked(api)).filter((a) => a.patient_id === jane.id)).toHaveLength(1)
    expect(await patientStatus(api, jane.id)).toBe("active")
    await expect(page.getByText("Jane Smith", { exact: true }).first()).toBeVisible()
  } finally {
    await context.close()
  }
})
