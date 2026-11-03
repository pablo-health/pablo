// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice's public booking page on the practice's own host.
 *
 * The domains practice holds working portal hosts on this stack
 * (backend/scripts/e2e_seed_practice_domains.py), and the frontend looks hosts
 * up (APP_HOSTS). What only the real stack can prove:
 *
 *   1. A stranger books through `/book/{link}` on the practice's primary host,
 *      in the theme of the practice's website, with every API call on that
 *      host — and the confirmation email links back to the same host, where
 *      the booking is confirmed.
 *   2. Only the practice's own active links are served there: another
 *      practice's link, a path no booking page has and a link that has been
 *      turned off all answer 404, and an alias sends visitors to the primary.
 *
 * The routing rules are pinned in
 * src/lib/portal-host/__tests__/practice-host.test.ts and the backend's answer
 * in backend/tests_integration/database/test_booking_hosts_db.py; this spec is
 * the wiring.
 */

import { type APIRequestContext, type Page, chromium } from "@playwright/test"
import JSZip from "jszip"
import type { ApiClient } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import { signInToFreshPractice } from "../fixtures/freshPractice"
import { firstLink, mail } from "../fixtures/mail"
import { giveBookingLink, giveWorkingHours } from "../fixtures/scenarios"
import { BASE_URL, PRACTICE_HOST_PORT } from "../fixtures/stack"

const PRIMARY = "portal.e2e-practice.example"
const ALIAS = "clients.e2e-practice.example"
/** The primary as a browser reaches it on this stack: through the balancer, plain http. */
const PRIMARY_ORIGIN = `http://${PRIMARY}:${PRACTICE_HOST_PORT}`
const ACCENT = "#24504c"
const THEME = { version: 1, colors: { accent: ACCENT }, fonts: {}, radius: "lg" }

/** Monday-based, matching the engine's `date.weekday()`. */
const BOOKABLE_WEEKDAY = 3 // Thursday

/** The next `BOOKABLE_WEEKDAY` at least two days out, as the day picker labels it: "Thu 12 Nov". */
function nextBookableDayLabel(): string {
  const day = new Date()
  day.setDate(day.getDate() + 2)
  while ((day.getDay() + 6) % 7 !== BOOKABLE_WEEKDAY) day.setDate(day.getDate() + 1)
  const weekday = day.toLocaleDateString("en-US", { weekday: "short" })
  return `${weekday} ${day.getDate()} ${day.toLocaleDateString("en-US", { month: "short" })}`
}

/** The domains practice with its portal on, its address minted, and PRIMARY its primary. */
async function domainsPractice(api: ApiClient): Promise<void> {
  await api.put("/api/portal/settings", { enabled: true })
  await api.post("/api/portal/practice-slug")
  await api.post(`/api/practice/domains/${PRIMARY}/primary`)
}

/** Publish a website whose theme.json gives the accent, from the Website settings page. */
async function publishThemedSite(page: Page): Promise<void> {
  const zip = new JSZip()
  zip.file("index.html", `<!doctype html><h1>Booking ${Date.now()}</h1>`)
  zip.file("theme.json", JSON.stringify(THEME))
  await page.goto("/dashboard/settings/website")
  await page.getByLabel("Website zip").setInputFiles({
    name: "site.zip",
    mimeType: "application/zip",
    buffer: await zip.generateAsync({ type: "nodebuffer" }),
  })
  await expect(page.getByTestId("website-draft")).toContainText("2 files")
  await page.getByRole("button", { name: "Publish" }).click()
  await expect(page.getByTestId("website-draft")).toHaveText("No draft yet.")
}

function onHost(request: APIRequestContext, host: string, path: string) {
  return request.get(`${BASE_URL}${path}`, { headers: { Host: host }, maxRedirects: 0, failOnStatusCode: false })
}

test("a client books on the practice's own host, in its theme @portal", async ({ browser }) => {
  const practice = await signInToFreshPractice(browser, "domains")
  const onThisMachine = await chromium.launch({
    args: ["--host-resolver-rules=MAP *.e2e-practice.example 127.0.0.1"],
  })
  const booker = `booker-${Date.now()}@example.com`
  try {
    await domainsPractice(practice.api)
    await publishThemedSite(practice.page)
    await giveWorkingHours(practice.api, BOOKABLE_WEEKDAY)
    const link = await giveBookingLink(practice.api, { host_name: "Sam Rivera", title: "First session" })

    const page = await onThisMachine.newPage()
    const apiOrigins = new Set<string>()
    page.on("request", (sent) => {
      const url = new URL(sent.url())
      if (url.pathname.startsWith("/api/")) apiOrigins.add(url.origin)
    })

    // The web app keeps the host's answer for a moment; wait for the one that
    // knows about the theme just published.
    await expect
      .poll(
        async () => {
          await page.goto(`${PRIMARY_ORIGIN}/book/${link.slug}`)
          await expect(page.getByRole("heading", { name: "First session" })).toBeVisible()
          return page.evaluate(() => {
            const scope = document.querySelector("[data-practice-theme]")
            return scope ? getComputedStyle(scope).getPropertyValue("--primary").trim() : null
          })
        },
        { message: "the booking page wears the website's accent", timeout: 60_000, intervals: [1_000, 2_000, 5_000] },
      )
      .toBe(ACCENT)

    await page.getByRole("button", { name: nextBookableDayLabel(), exact: true }).click()
    await page.getByRole("button", { name: /^\d{1,2}:\d{2} [AP]M$/ }).first().click()
    await page.getByLabel("First name").fill("Rowan")
    await page.getByLabel("Last name").fill("Ellery")
    await page.getByLabel("Email").fill(booker)
    await page.getByRole("button", { name: "Confirm booking" }).click()
    await expect(page.getByRole("heading", { name: "Almost there" })).toBeVisible()
    expect(apiOrigins, "every API call stayed on the practice's own origin").toEqual(new Set([PRIMARY_ORIGIN]))

    const confirmLink = firstLink(await mail.waitFor(booker))
    expect(confirmLink, "the email links to the practice's own host").toMatch(
      new RegExp(`^https://${PRIMARY.replaceAll(".", "\\.")}/book/${link.slug}/confirm\\?token=`),
    )
    await page.goto(confirmLink.replace(`https://${PRIMARY}`, PRIMARY_ORIGIN))
    await expect(page.getByRole("heading", { name: "You're booked" })).toBeVisible()
    await expect(page.locator("[data-practice-theme]")).toHaveCount(1)
  } finally {
    await onThisMachine.close()
    await practice.context.close()
  }
})

test("the practice's own host serves only its own active booking links @portal", async ({
  api: sharedApi,
  browser,
  request,
}) => {
  const practice = await signInToFreshPractice(browser, "domains")
  try {
    await domainsPractice(practice.api)
    const own = await giveBookingLink(practice.api)
    const theirs = await giveBookingLink(sharedApi)

    expect((await onHost(request, PRIMARY, `/book/${own.slug}`)).status(), "the practice's link").toBe(200)

    const alias = await onHost(request, ALIAS, `/book/${own.slug}/manage?token=t`)
    expect(alias.status(), "an alias").toBe(301)
    expect(alias.headers()["location"]).toBe(`https://${PRIMARY}/book/${own.slug}/manage?token=t`)

    for (const path of [`/book/${theirs.slug}`, `/book/${theirs.slug}/confirm`, `/book/${own.slug}/other`, "/book"]) {
      const refused = await onHost(request, PRIMARY, path)
      expect(refused.status(), path).toBe(404)
      expect(refused.headers()["location"], `${path} is not a redirect`).toBeUndefined()
    }

    await practice.api.patch(`/api/booking-links/${own.id}`, { is_active: false })
    await expect
      .poll(async () => (await onHost(request, PRIMARY, `/book/${own.slug}`)).status(), {
        message: "a link turned off is no longer served",
        timeout: 30_000,
      })
      .toBe(404)
  } finally {
    await practice.context.close()
  }
})
