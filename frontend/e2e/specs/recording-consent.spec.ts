// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Starting a recording from Today respects the client's answer about
 * AI-assisted notes, while the practice asks its clients about them.
 *
 * - Declined: "Start session" says so, with the date, and links to the chart;
 *   the session start the desktop app would make is refused by the server
 *   with CLIENT_DECLINED_AI_NOTES.
 * - Not asked yet: "Start session" asks first. "Client agreed today" puts a
 *   dated answer on the chart and then hands off to the desktop app. "Record
 *   anyway" hands off an intent that tells the desktop app it was asked, so
 *   the app does not ask again.
 *
 * The desktop app is the one thing the stack cannot run. Two of its edges
 * are stood in for in the browser, and nothing else: the list of enrolled
 * installs (enrolling one takes the app's own sign-in) and the launch intent,
 * whose handoff link is pointed at a page that only records it was reached.
 * The "Record anyway" test lets the intent through to the real server and
 * redeems it the way the app does. The consent record, the setting, the chart
 * and the server's refusal are all the real stack.
 *
 * In a practice of its own (fixtures/freshPractice.ts): the answer only
 * counts while the practice asks, and the spec about that setting turns it
 * off in the shared practice for a while.
 */

import type { Page, Route } from "@playwright/test"
import { ApiError, type ApiClient } from "../fixtures/api"
import { test, expect } from "../fixtures/auth"
import { BROWSER_TIME_ZONE } from "../fixtures/clock"
import { signInToFreshPractice, type FreshPractice } from "../fixtures/freshPractice"
import { givePatient, type Appointment, type Patient } from "../fixtures/scenarios"

test.describe.configure({ mode: "serial", retries: 0 })

/** The handoff link the stand-in launch intent hands out. */
const HANDOFF_PATH = "/e2e-desktop-handoff"

/** "Start session" is offered on a Mac with the desktop app enrolled. */
const MAC_USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

/** A civil date as the chart shows it, on the browser's (and the clinician's) calendar. */
function shown(at: Date): string {
  return at.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: BROWSER_TIME_ZONE,
  })
}

/** The same date as an answer is recorded with it, YYYY-MM-DD. */
function isoDay(at: Date): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: BROWSER_TIME_ZONE }).format(at)
}

/**
 * `hour`:00 today on the browser's calendar, as an instant.
 *
 * Not "now plus a few minutes": Today lists the sessions on the clinician's
 * calendar day, and for the hour around midnight in that zone an offset from
 * now lands on yesterday or tomorrow and the row is not there to click.
 */
function todayAt(hour: number): Date {
  const guess = new Date(`${isoDay(new Date())}T${String(hour).padStart(2, "0")}:00:00Z`)
  const zonedHour = Number(
    new Intl.DateTimeFormat("en-US", {
      timeZone: BROWSER_TIME_ZONE,
      hour: "numeric",
      hourCycle: "h23",
    }).format(guess),
  )
  const behindBy = (hour - zonedHour + 24) % 24
  return new Date(guess.getTime() + behindBy * 60 * 60 * 1000)
}

/** A session today, twenty minutes long, starting at `hour`:00 on the browser's calendar. */
async function giveSessionToday(
  api: ApiClient,
  patient: Patient,
  hour: number,
): Promise<Appointment> {
  const start = todayAt(hour)
  const end = new Date(start.getTime() + 20 * 60 * 1000)
  return api.post<Appointment>("/api/appointments", {
    patient_id: patient.id,
    title: `${patient.first_name} ${patient.last_name}`,
    start_at: start.toISOString(),
    end_at: end.toISOString(),
    duration_minutes: 20,
    session_type: "individual",
  })
}

/** Stand in for the desktop app's two edges; see the header. */
async function standInForTheDesktopApp(page: Page): Promise<void> {
  await page.route("**/api/users/me/devices", (route) =>
    route.fulfill({
      json: [
        {
          install_id: "e2e-install",
          platform: "mac",
          os_version: "15.0",
          enrolled_at: "2026-01-01T00:00:00Z",
          last_seen: "2026-01-01T00:00:00Z",
          jkt_fingerprint: null,
        },
      ],
    }),
  )
  await page.route("**/api/launch/intent", (route) =>
    route.fulfill({
      json: {
        intent_id: "e2e-intent",
        launch_url: new URL(HANDOFF_PATH, page.url()).toString(),
        expires_in: 180,
      },
    }),
  )
  await page.route(`**${HANDOFF_PATH}`, (route) =>
    route.fulfill({ contentType: "text/html", body: "<p>Handed off</p>" }),
  )
}

let practice: FreshPractice

test.beforeAll(async ({ browser }) => {
  practice = await signInToFreshPractice(browser, "consent", {
    timezoneId: BROWSER_TIME_ZONE,
    userAgent: MAC_USER_AGENT,
  })
  const { api, page } = practice
  await api.put("/api/users/me/practice/ai-notes-consent", { ask_clients_about_ai_notes: true })
  const preferences = await api.get<Record<string, unknown>>("/api/users/me/preferences")
  await api.put("/api/users/me/preferences", {
    ...preferences,
    calendar_setup_complete: true,
    timezone: BROWSER_TIME_ZONE,
  })
  await standInForTheDesktopApp(page)
})

test.afterAll(async () => {
  await practice?.context.close()
})

/** Today on the dashboard, and the row for this client's session. */
async function startSessionFor(page: Page, patient: Patient): Promise<void> {
  await page.goto("/dashboard")
  const row = page
    .getByRole("listitem")
    .filter({ hasText: `${patient.first_name} ${patient.last_name}` })
  await row.getByRole("link", { name: "Start session" }).click()
}

test("a client who declined is not recorded, and the chart says when", async () => {
  const { page, api } = practice
  const patient = await givePatient(api)
  const declinedOn = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000)
  await api.post(`/api/patients/${patient.id}/ai-consent`, {
    decision: "declined",
    effective_on: isoDay(declinedOn),
  })
  const appointment = await giveSessionToday(api, patient, 9)
  try {
    await startSessionFor(page, patient)

    const dialog = page.getByRole("dialog", { name: "AI-assisted notes declined" })
    await expect(dialog).toContainText(
      `This client declined AI-assisted notes on ${shown(declinedOn)}.`,
    )
    await expect(page).not.toHaveURL(new RegExp(HANDOFF_PATH))

    // What the desktop app would ask next is refused by the server itself.
    const refused = await api
      .post(`/api/appointments/${appointment.id}/start-session`, {})
      .then(
        () => null,
        (error: unknown) => error,
      )
    expect(refused).toBeInstanceOf(ApiError)
    expect((refused as ApiError).status).toBe(403)
    expect(JSON.parse((refused as ApiError).body).error.code).toBe("CLIENT_DECLINED_AI_NOTES")

    await dialog.getByRole("link", { name: "Open chart" }).click()
    await expect(page).toHaveURL(new RegExp(`/dashboard/patients/${patient.id}`))
    await expect(page.getByTestId("ai-consent-line")).toHaveText(
      `AI notes: declined ${shown(declinedOn)}`,
    )
  } finally {
    await api.delete(`/api/appointments/${appointment.id}`)
  }
})

test("with nothing on file, 'Client agreed today' records it and starts", async () => {
  const { page, api } = practice
  const patient = await givePatient(api)
  const appointment = await giveSessionToday(api, patient, 15)
  try {
    await startSessionFor(page, patient)

    const dialog = page.getByRole("dialog", { name: "No consent on file" })
    await dialog.getByRole("button", { name: "Client agreed today" }).click()
    await expect(page).toHaveURL(new RegExp(HANDOFF_PATH))

    await page.goto(`/dashboard/patients/${patient.id}`)
    await expect(page.getByTestId("ai-consent-line")).toHaveText(
      `AI notes: agreed ${shown(new Date())}`,
    )
  } finally {
    await api.delete(`/api/appointments/${appointment.id}`)
  }
})

interface Redeemed {
  appointment_id: string
  ai_consent_prompted: boolean
}

test("with nothing on file, 'Record anyway' tells the desktop app it was asked", async () => {
  const { page, api } = practice
  const patient = await givePatient(api)
  const appointment = await giveSessionToday(api, patient, 15)

  // The real server issues the intents; only the link is pointed at the stand-in page.
  const issued: { intentId: string; prompted: boolean }[] = []
  const issueForReal = async (route: Route) => {
    const response = await route.fetch()
    const body = (await response.json()) as { intent_id: string }
    issued.push({
      intentId: body.intent_id,
      prompted: route.request().postDataJSON().ai_consent_prompted === true,
    })
    await route.fulfill({
      response,
      json: { ...body, launch_url: new URL(HANDOFF_PATH, page.url()).toString() },
    })
  }
  await page.route("**/api/launch/intent", issueForReal)
  try {
    await startSessionFor(page, patient)

    const dialog = page.getByRole("dialog", { name: "No consent on file" })
    await dialog.getByRole("button", { name: "Record anyway" }).click()
    await expect(page).toHaveURL(new RegExp(HANDOFF_PATH))

    // Redeemed as the desktop app does when the link opens it.
    const prompted = issued.find((intent) => intent.prompted)
    expect(prompted).toBeDefined()
    const handedOff = await api.post<Redeemed>("/api/launch/redeem", {
      intent_id: prompted?.intentId,
    })
    expect(handedOff).toMatchObject({ appointment_id: appointment.id, ai_consent_prompted: true })

    // The intent a plain start would have handed off does not say so.
    const plain = issued.find((intent) => !intent.prompted)
    expect(plain).toBeDefined()
    const unprompted = await api.post<Redeemed>("/api/launch/redeem", {
      intent_id: plain?.intentId,
    })
    expect(unprompted.ai_consent_prompted).toBe(false)

    // Nothing was put on the chart.
    const record = await api.get<{ current: unknown }>(`/api/patients/${patient.id}/ai-consent`)
    expect(record.current).toBeNull()
  } finally {
    await page.unroute("**/api/launch/intent", issueForReal)
    await api.delete(`/api/appointments/${appointment.id}`)
  }
})
