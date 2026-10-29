// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A clinician sets away dates and reviews the reply; a client who writes
 * through the portal meanwhile gets it once.
 *
 * The clinician walks Settings > Away the way they would: the dates, then the
 * reply shown for review with the switch on and the 911 and 988 lines beside
 * it, then save. The client signs in to the portal, starts a conversation and
 * writes a second time into it, and the thread holds exactly one message from
 * the practice — the reviewed reply with the safety lines after it.
 *
 * In a practice of its own (fixtures/freshPractice.ts): away dates in the
 * shared practice would acknowledge every other portal spec's messages.
 */

import { expect, test } from "../fixtures/auth"
import { signInToFreshPractice, type FreshPractice } from "../fixtures/freshPractice"
import {
  givePortalContactDetails,
  givePortalInvitation,
  givePortalSession,
  openPortalSection,
  signInToPortal,
} from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"

test.describe.configure({ mode: "serial", retries: 0 })

/** A calendar day `offset` days from today, as the date input takes it. */
function isoDay(offset: number): string {
  const day = new Date()
  day.setDate(day.getDate() + offset)
  const month = String(day.getMonth() + 1).padStart(2, "0")
  const date = String(day.getDate()).padStart(2, "0")
  return `${day.getFullYear()}-${month}-${date}`
}

let practice: FreshPractice

test.beforeAll(async ({ browser }) => {
  practice = await signInToFreshPractice(browser, "away")
  await practice.api.put("/api/portal/settings", {
    enabled: true,
    modules: { messaging: true, refills: true },
  })
})

test.afterAll(async () => {
  // Ended so a re-run against the same stack starts from no away dates.
  const listed = await practice.api.get<{ periods: { id: string }[] }>("/api/away-periods")
  for (const period of listed.periods) {
    await practice.api.post(`/api/away-periods/${period.id}/end`)
  }
  await practice?.context.close()
})

test("away dates are reviewed, and a client writing in gets one acknowledgment @portal", async ({
  page,
}) => {
  const clinician = practice.page
  await clinician.goto("/dashboard/settings/away")
  await clinician.getByTestId("away-add").click()
  // A day of slack either side, so the clinician's zone and the runner's agree.
  await clinician.getByTestId("away-start").fill(isoDay(-1))
  await clinician.getByTestId("away-end").fill(isoDay(4))
  await clinician.getByTestId("away-next").click()

  const reply = clinician.getByTestId("away-reply-text")
  await expect(reply).toHaveValue(/is away and back on /)
  await expect(clinician.getByRole("switch", { name: "Reply to clients while you're away" }))
    .toHaveAttribute("aria-checked", "true")
  const safety = clinician.getByTestId("away-safety-lines")
  await expect(safety).toContainText("If this is an emergency, call 911.")
  await expect(safety).toContainText("988")
  const reviewed = await reply.inputValue()

  const saved = clinician.waitForResponse(
    (r) => r.url().endsWith("/api/away-periods") && r.request().method() === "POST",
  )
  await clinician.getByTestId("away-save").click()
  expect((await saved).status()).toBe(201)
  await expect(clinician.getByTestId("away-periods")).toContainText("Away now")

  const { email, phone } = givePortalContactDetails()
  const client = await givePatient(practice.api, { email, phone })
  const invitation = await givePortalInvitation(practice.api, client.id, email, phone)
  await signInToPortal(page, invitation)
  await openPortalSection(page, "messaging")

  await page.getByTestId("portal-messaging-start-thread").click()
  await page.getByTestId("portal-messaging-new-thread-subject").fill("This week")
  await page.getByTestId("portal-messaging-new-thread-body").fill("Can we move Thursday?")
  await page.getByTestId("portal-messaging-new-thread-send").click()

  const thread = page.getByTestId("portal-messaging-thread-view")
  const fromPractice = thread.locator('[data-sender="practice"]')
  await expect(fromPractice).toHaveCount(1)
  await expect(fromPractice).toContainText(reviewed)
  await expect(fromPractice).toContainText("If this is an emergency, call 911.")
  await expect(fromPractice).toContainText("988")

  await page.getByTestId("portal-messaging-composer-body").fill("One more thing.")
  await page.getByTestId("portal-messaging-composer-send").click()
  await expect(thread).toContainText("One more thing.")
  await expect(fromPractice, "one acknowledgment per conversation").toHaveCount(1)
})

test("a refill asked for while the clinician is away says when it will be reviewed @portal", async ({
  request,
}) => {
  const { email, phone } = givePortalContactDetails()
  const client = await givePatient(practice.api, { email, phone })
  const session = await givePortalSession(practice.api, request, client.id, email, phone)

  const asked = await request.post(`${BACKEND_URL}/api/patient/refills`, {
    headers: { Authorization: `Bearer ${session}` },
    data: { medication_text: "Sertraline 50 mg" },
  })

  expect(asked.status(), "asking for a refill").toBe(201)
  const said = (await asked.json()).away_acknowledgment as string
  expect(said).toMatch(/^Your request is in\. It will be reviewed by [A-Z][a-z]+day, /)
  expect(said).toContain("If this is an emergency, call 911.")
  expect(said).toContain("988")
})
