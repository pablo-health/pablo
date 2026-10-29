// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A clinician going away asks a colleague to cover; the colleague accepts,
 * opens a covered client's record during the dates, and loses it the moment
 * the period ends. The clinician then sees what the colleague did.
 *
 * Both clinicians use Settings > Away the way they would. Access is checked
 * through the API as well as the chart page, because the chart's own "not
 * found" is the API's 404 underneath.
 *
 * In a practice of its own with two clinicians (fixtures/freshPractice.ts,
 * "coverage"): away dates in the shared practice would acknowledge every
 * other portal spec's messages.
 */

import { expect, test } from "../fixtures/auth"
import { ApiError, type ApiClient } from "../fixtures/api"
import { signInAs, signInToFreshPractice, type FreshPractice } from "../fixtures/freshPractice"
import { givePatient } from "../fixtures/scenarios"

test.describe.configure({ mode: "serial", retries: 0 })

const AWAY_NAME = "Robin Avery"
const COVER_NAME = "Sam Okafor"
const COLLEAGUE = {
  email: "e2e-fresh-coverage-colleague@example.com",
  password: "E2e-fresh-coverage-colleague-password-long-enough",
}

/** A calendar day `offset` days from today, as the date input takes it. */
function isoDay(offset: number): string {
  const day = new Date()
  day.setDate(day.getDate() + offset)
  const month = String(day.getMonth() + 1).padStart(2, "0")
  const date = String(day.getDate()).padStart(2, "0")
  return `${day.getFullYear()}-${month}-${date}`
}

async function chartStatus(api: ApiClient, patientId: string): Promise<number> {
  return api.get(`/api/patients/${patientId}`).then(
    () => 200,
    (error: unknown) => {
      if (error instanceof ApiError) return error.status
      throw error
    },
  )
}

async function endEverything(practice: FreshPractice): Promise<void> {
  const listed = await practice.api.get<{ periods: { id: string }[] }>("/api/away-periods")
  for (const period of listed.periods) {
    await practice.api.post(`/api/away-periods/${period.id}/end`)
  }
}

let away: FreshPractice
let cover: FreshPractice

test.beforeAll(async ({ browser }) => {
  away = await signInToFreshPractice(browser, "coverage")
  cover = await signInAs(browser, COLLEAGUE.email, COLLEAGUE.password)
  // The colleague picker lists colleagues by the name they entered.
  await away.api.patch("/api/users/me", { name: AWAY_NAME })
  await cover.api.patch("/api/users/me", { name: COVER_NAME })
  // A re-run against the same stack starts from no away dates.
  await endEverything(away)
})

test.afterAll(async () => {
  if (away) await endEverything(away)
  await away?.context.close()
  await cover?.context.close()
})

test("a colleague covers only after accepting, and only until the period ends", async () => {
  const client = await givePatient(away.api, { first_name: "Covered", last_name: "Client" })
  expect(await chartStatus(cover.api, client.id), "before anybody asked").toBe(404)

  // The away clinician sets the dates and asks the colleague.
  const clinician = away.page
  await clinician.goto("/dashboard/settings/away")
  await clinician.getByTestId("away-add").click()
  // A day of slack either side, so the clinician's zone and the runner's agree.
  await clinician.getByTestId("away-start").fill(isoDay(-1))
  await clinician.getByTestId("away-end").fill(isoDay(4))
  await clinician.getByTestId("away-covering").selectOption({ label: COVER_NAME })
  await clinician.getByTestId("away-next").click()
  await expect(clinician.getByTestId("away-reply-text")).toHaveValue(/is away and back on /)
  await clinician.getByTestId("away-save").click()
  await expect(clinician.getByTestId("away-periods")).toContainText(
    `Waiting for ${COVER_NAME} to accept`,
  )
  expect(await chartStatus(cover.api, client.id), "asked, not yet accepted").toBe(404)

  // The colleague accepts.
  const colleague = cover.page
  await colleague.goto("/dashboard/settings/away")
  const requests = colleague.getByTestId("coverage-requests")
  await expect(requests).toContainText(AWAY_NAME)
  await requests.locator('[data-testid^="coverage-accept-"]').click()
  await expect(requests).toContainText("Covering")

  // During the dates the colleague opens the client's record.
  expect(await chartStatus(cover.api, client.id), "accepted, inside the dates").toBe(200)
  await colleague.goto(`/dashboard/patients/${client.id}`)
  await expect(colleague.getByText("Covered Client").first()).toBeVisible()

  // The away clinician comes back early; access ends with it.
  await clinician.reload()
  await expect(clinician.getByTestId("away-periods")).toContainText(`${COVER_NAME} covering`)
  await clinician.locator('[data-testid^="away-end-"]').click()
  await expect(clinician.getByTestId("away-recently-covered")).toBeVisible()
  expect(await chartStatus(cover.api, client.id), "after the period ended").toBe(404)

  // And sees what the colleague did.
  await clinician.getByRole("button", { name: `What ${COVER_NAME} did` }).click()
  const activity = clinician.locator('[data-testid^="coverage-activity-"]')
  await expect(activity).toContainText("Covered Client")
  await expect(activity).toContainText("Opened the record")
})
