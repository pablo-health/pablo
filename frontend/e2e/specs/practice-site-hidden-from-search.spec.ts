// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice asks search engines not to list its website, from Settings >
 * Website, and every visitor's answer on the website's hosts says so.
 *
 * The domains practice holds two working website hosts, the bare domain as
 * primary and its www alias (backend/scripts/e2e_seed_practice_domains.py).
 * Through the real page and the real backend, the owner turns the setting on
 * and publishes a site that carries its own robots.txt; the site is then asked
 * for through the stand-in load balancer (practice-host-lb) under its own host
 * names, the way a crawler reaches it:
 *
 *   - a page, a missing page and the www redirect all carry
 *     `X-Robots-Tag: noindex, nofollow`;
 *   - `/robots.txt` disallows everything on both hosts, in place of the one the
 *     website uploaded, and www answers it itself rather than redirecting;
 *   - turned off again, the website's own robots.txt is back and nothing says
 *     noindex.
 *
 * The rules are pinned in unit tests: the routing and headers in
 * src/lib/portal-host/__tests__/practice-site.test.ts and
 * practice-site-lookup.test.ts, the setting and its audit in
 * backend/tests_integration/database/test_practice_sites_db.py. This spec is
 * the wiring. The setting is turned off again however the spec ends, since
 * every other website spec uses the same practice.
 */

import type { APIRequestContext } from "@playwright/test"
import JSZip from "jszip"
import { expect, test } from "../fixtures/auth"
import { signInToFreshPractice } from "../fixtures/freshPractice"
import { PRACTICE_HOST_PORT } from "../fixtures/stack"

const SITE = "e2e-site.example"
const WWW = "www.e2e-site.example"
const BALANCER_URL = `http://127.0.0.1:${PRACTICE_HOST_PORT}`
const SETTINGS_PATH = "/dashboard/settings/website"
const NOINDEX = "noindex, nofollow"
const OWN_ROBOTS = "User-agent: *\nAllow: /\n"
const DISALLOW_ALL = "User-agent: *\nDisallow: /\n"
/** Host answers are kept for a minute on each side; a change reaches visitors within it. */
const WITHIN_THE_MINUTE = { timeout: 90_000, intervals: [500, 1_000, 2_000] }

async function siteZip(marker: string) {
  const zip = new JSZip()
  zip.file("index.html", `<!doctype html><h1>${marker}</h1>`)
  zip.file("robots.txt", OWN_ROBOTS)
  zip.file("404.html", "<h1>Not here</h1>")
  return { name: "site.zip", mimeType: "application/zip", buffer: await zip.generateAsync({ type: "nodebuffer" }) }
}

function onSite(request: APIRequestContext, host: string, path: string) {
  return request.get(`${BALANCER_URL}${path}`, {
    headers: { Host: `${host}:${PRACTICE_HOST_PORT}` },
    maxRedirects: 0,
    failOnStatusCode: false,
  })
}

test("a practice hides its website from search engines and every visitor's answer says so @portal", async ({
  browser,
  request,
}) => {
  const { page, context, api } = await signInToFreshPractice(browser, "domains")
  const marker = `Hidden ${Date.now()}`
  try {
    await page.goto(SETTINGS_PATH)
    const toggle = page.getByRole("switch", { name: "Hide from search engines" })
    await expect(toggle).toHaveAttribute("aria-checked", "false")
    await toggle.click()
    await expect(toggle).toHaveAttribute("aria-checked", "true")

    await page.getByLabel("Website zip").setInputFiles(await siteZip(marker))
    await expect(page.getByTestId("website-draft")).toContainText("3 files")
    await page.getByRole("button", { name: "Publish" }).click()
    await expect(page.getByTestId("website-live")).toContainText(`Live at ${SITE}`)

    // The setting holds across a reload: it is the server's, not the page's.
    await page.reload()
    await expect(page.getByRole("switch", { name: "Hide from search engines" })).toHaveAttribute(
      "aria-checked",
      "true",
    )

    await expect
      .poll(async () => {
        const home = await onSite(request, SITE, "/")
        return { body: (await home.text()).includes(marker), robots: home.headers()["x-robots-tag"] }
      }, { message: "the published site says noindex", ...WITHIN_THE_MINUTE })
      .toEqual({ body: true, robots: NOINDEX })

    const missing = await onSite(request, SITE, "/no-such-page")
    expect(missing.status()).toBe(404)
    expect(await missing.text()).toContain("Not here")
    expect(missing.headers()["x-robots-tag"]).toBe(NOINDEX)

    const www = await onSite(request, WWW, "/about/")
    expect(www.status()).toBe(301)
    expect(www.headers()["location"]).toBe(`https://${SITE}/about/`)
    expect(www.headers()["x-robots-tag"]).toBe(NOINDEX)

    for (const host of [SITE, WWW]) {
      const robots = await onSite(request, host, "/robots.txt")
      expect(robots.status(), `${host} answers robots.txt itself`).toBe(200)
      expect(await robots.text(), `${host} disallows everything`).toBe(DISALLOW_ALL)
      expect(robots.headers()["content-type"]).toBe("text/plain; charset=utf-8")
      expect(robots.headers()["x-robots-tag"]).toBe(NOINDEX)
    }

    // Turned off, the website's own robots.txt is back and nothing says noindex.
    await page.getByRole("switch", { name: "Hide from search engines" }).click()
    await expect(page.getByRole("switch", { name: "Hide from search engines" })).toHaveAttribute(
      "aria-checked",
      "false",
    )
    await expect
      .poll(async () => {
        const robots = await onSite(request, SITE, "/robots.txt")
        return { body: await robots.text(), robots: robots.headers()["x-robots-tag"] ?? null }
      }, { message: "the website's own robots.txt is served again", ...WITHIN_THE_MINUTE })
      .toEqual({ body: OWN_ROBOTS, robots: null })
    const home = await onSite(request, SITE, "/")
    expect(home.headers()["x-robots-tag"]).toBeUndefined()
  } finally {
    await api.put("/api/practice/website/search", { hide_from_search: false })
    await context.close()
  }
})
