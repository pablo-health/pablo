// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice's "Book a session" link, on its website and in its portal.
 *
 * The domains practice holds a portal host and a website host on this stack
 * (backend/scripts/e2e_seed_practice_domains.py). From Settings > Website the
 * owner chooses where the link goes and which surfaces show it. What only the
 * real stack can prove:
 *
 *   1. Pointed at one of the practice's booking links, the link the website
 *      marks data-pablo="book" goes to the booking page on the practice's own
 *      host, and the portal's header shows it; turning the booking link off
 *      takes it off both.
 *   2. Pointed at a booking page the practice uses elsewhere and shown on the
 *      website only, the website links there and the portal shows nothing;
 *      switched off, the website shows nothing either.
 *
 * The rules are pinned in backend/tests/test_booking_button.py and
 * backend/tests_integration/database/test_booking_hosts_db.py; this spec is
 * the wiring.
 */

import { type APIRequestContext, type Page, chromium } from "@playwright/test"
import JSZip from "jszip"
import type { ApiClient } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"
import { signInToFreshPractice } from "../fixtures/freshPractice"
import { giveBookingLink } from "../fixtures/scenarios"
import { PRACTICE_HOST_PORT } from "../fixtures/stack"

const PRIMARY = "portal.e2e-practice.example"
const PRIMARY_ORIGIN = `http://${PRIMARY}:${PRACTICE_HOST_PORT}`
/** The practice's primary website host. */
const SITE = "e2e-site.example"
const BALANCER_URL = `http://127.0.0.1:${PRACTICE_HOST_PORT}`
const SETTINGS_PATH = "/dashboard/settings/website"
const OUTSIDE = "https://book.example.net/e2e-practice"

/** The domains practice with its portal on, its address minted, and PRIMARY its primary. */
async function domainsPractice(api: ApiClient): Promise<string> {
  await api.put("/api/portal/settings", { enabled: true })
  const { slug } = await api.post<{ slug: string }>("/api/portal/practice-slug")
  await api.post(`/api/practice/domains/${PRIMARY}/primary`)
  return slug
}

async function publishSite(page: Page): Promise<void> {
  const zip = new JSZip()
  zip.file(
    "index.html",
    `<!doctype html><html><body><h1>Book ${Date.now()}</h1>` +
      `<a class="button" href="#contact" data-pablo="book">Book a visit</a></body></html>`,
  )
  await page.goto(SETTINGS_PATH)
  await page.getByLabel("Website zip").setInputFiles({
    name: "site.zip",
    mimeType: "application/zip",
    buffer: await zip.generateAsync({ type: "nodebuffer" }),
  })
  await expect(page.getByTestId("website-draft")).toContainText("1 file,")
  await page.getByRole("button", { name: "Publish" }).click()
  await expect(page.getByTestId("website-draft")).toHaveText("No draft yet.")
}

/** Where the website's marked link goes, or null when it is taken out. */
async function websiteLink(request: APIRequestContext): Promise<string | null> {
  const home = await request.get(`${BALANCER_URL}/`, {
    headers: { Host: `${SITE}:${PRACTICE_HOST_PORT}` },
    maxRedirects: 0,
    failOnStatusCode: false,
  })
  const match = /<a href="([^"]*)" class="button" data-pablo="book">Book a visit<\/a>/.exec(await home.text())
  return match ? match[1] : null
}

/** Where the portal links "Book a session", or null for no link. */
async function portalLink(api: ApiClient, slug: string): Promise<string | null> {
  return (await api.get<{ booking_url: string | null }>(`/api/portal/practices/${slug}`)).booking_url
}

/** Set the card on Settings > Website and save it. */
async function chooseOnCard(
  page: Page,
  where: string,
  switches: { website: boolean; portal: boolean },
  address?: string,
): Promise<void> {
  await page.goto(SETTINGS_PATH)
  await page.getByRole("combobox", { name: "Where it goes" }).click()
  await page.getByRole("option", { name: where }).click()
  if (address !== undefined) await page.getByLabel("Booking page address").fill(address)
  for (const [name, on] of [
    ["On your website", switches.website],
    ["In your client portal", switches.portal],
  ] as const) {
    const toggle = page.getByRole("switch", { name })
    if ((await toggle.getAttribute("aria-checked")) !== String(on)) await toggle.click()
  }
  const saved = page.waitForResponse(
    (r) => r.url().endsWith("/api/practice/booking-button") && r.request().method() === "PUT",
  )
  await page.getByRole("button", { name: "Save" }).click()
  expect((await saved).status(), "the card saved").toBe(200)
}

const settled = { timeout: 30_000, intervals: [500, 1_000, 2_000] }

test("the link goes to the practice's booking page, and leaves with it @portal", async ({ browser, request }) => {
  const practice = await signInToFreshPractice(browser, "domains")
  const onThisMachine = await chromium.launch({
    args: ["--host-resolver-rules=MAP *.e2e-practice.example 127.0.0.1"],
  })
  try {
    const slug = await domainsPractice(practice.api)
    await publishSite(practice.page)
    const link = await giveBookingLink(practice.api, { title: `First session ${Date.now()}` })
    const booking = `https://${PRIMARY}/book/${link.slug}`

    await chooseOnCard(practice.page, `${link.title} · /book/${link.slug}`, { website: true, portal: true })

    await expect.poll(() => websiteLink(request), settled).toBe(booking)
    expect(await portalLink(practice.api, slug)).toBe(booking)

    const portal = await onThisMachine.newPage()
    await portal.goto(`${PRIMARY_ORIGIN}/`)
    await expect(portal.getByTestId("portal-shell-no-session")).toBeVisible()
    await expect(portal.getByRole("link", { name: "Book a session" })).toHaveAttribute("href", booking)

    // The booking link turned off: gone from both, with the setting untouched.
    await practice.api.patch(`/api/booking-links/${link.id}`, { is_active: false })
    await expect.poll(() => websiteLink(request), settled).toBeNull()
    expect(await portalLink(practice.api, slug)).toBeNull()
    await portal.reload()
    await expect(portal.getByTestId("portal-shell-no-session")).toBeVisible()
    await expect(portal.getByTestId("portal-book-session")).toHaveCount(0)

    await practice.page.goto(SETTINGS_PATH)
    await expect(practice.page.getByTestId("book-session-link-off")).toBeVisible()
  } finally {
    await onThisMachine.close()
    await practice.context.close()
  }
})

test("the link goes to a booking page used elsewhere, only where it is on @portal", async ({ browser, request }) => {
  const practice = await signInToFreshPractice(browser, "domains")
  try {
    const slug = await domainsPractice(practice.api)
    await publishSite(practice.page)

    await chooseOnCard(practice.page, "A booking page you use elsewhere", { website: true, portal: false }, OUTSIDE)
    await expect.poll(() => websiteLink(request), settled).toBe(OUTSIDE)
    expect(await portalLink(practice.api, slug)).toBeNull()

    await chooseOnCard(practice.page, "A booking page you use elsewhere", { website: false, portal: false }, OUTSIDE)
    await expect.poll(() => websiteLink(request), settled).toBeNull()
  } finally {
    await practice.context.close()
  }
})
