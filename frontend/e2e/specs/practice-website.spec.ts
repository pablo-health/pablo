// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice's website, published from Settings > Website and served on the
 * practice's own website host.
 *
 * The domains practice holds two working website hosts, the bare domain as
 * primary and its www alias (backend/scripts/e2e_seed_practice_domains.py).
 * Through the real page, the real backend and the stack's stand-in storage,
 * the owner uploads a zip, previews the draft, publishes, publishes again and
 * rolls back; after each step the site is asked for through the stand-in load
 * balancer (practice-host-lb) under the website's own host name, the way a
 * visitor's browser reaches it. Along the way:
 *
 *   - a page and a stylesheet serve with their types, `/about` goes to
 *     `/about/`, a missing page is the site's own 404 page, and nothing the
 *     app sets (a cookie, its policy for its own pages) reaches the website;
 *   - www sends visitors to the primary, path and query kept;
 *   - the practice's portal host still serves the portal.
 *
 * The rules themselves are pinned in unit tests: the zip and path rules in
 * backend/tests/test_site_files.py and test_site_paths.py, the routing in
 * src/lib/portal-host/__tests__/practice-site.test.ts. This spec is the wiring.
 */

import type { APIRequestContext, Page } from "@playwright/test"
import JSZip from "jszip"
import { expect, test } from "../fixtures/auth"
import { signInToFreshPractice } from "../fixtures/freshPractice"
import { PRACTICE_HOST_PORT } from "../fixtures/stack"

const SITE = "e2e-site.example"
const WWW = "www.e2e-site.example"
const PORTAL = "portal.e2e-practice.example"
const BALANCER_URL = `http://127.0.0.1:${PRACTICE_HOST_PORT}`
const SETTINGS_PATH = "/dashboard/settings/website"

/** The site under one folder, as a practice zips the folder it built. */
async function siteZip(marker: string): Promise<{ name: string; mimeType: string; buffer: Buffer }> {
  const zip = new JSZip()
  const folder = zip.folder("my-site")
  if (!folder) throw new Error("jszip made no folder")
  folder.file("index.html", `<!doctype html><link rel="stylesheet" href="css/site.css"><h1>${marker}</h1>`)
  folder.file("css/site.css", "h1 { color: teal; }")
  folder.file("about/index.html", `<h1>About ${marker}</h1>`)
  folder.file("404.html", "<h1>Not here</h1>")
  folder.file("app.js", "document.title = 'scripted'")
  return { name: "site.zip", mimeType: "application/zip", buffer: await zip.generateAsync({ type: "nodebuffer" }) }
}

function onSite(request: APIRequestContext, host: string, path: string) {
  return request.get(`${BALANCER_URL}${path}`, {
    headers: { Host: `${host}:${PRACTICE_HOST_PORT}` },
    maxRedirects: 0,
    failOnStatusCode: false,
  })
}

/** Wait for the home page on the website's host to show `marker`. */
async function expectLive(request: APIRequestContext, marker: string): Promise<void> {
  await expect
    .poll(async () => (await onSite(request, SITE, "/")).text(), {
      message: `the site serves ${marker}`,
      timeout: 90_000,
      intervals: [500, 1_000, 2_000],
    })
    .toContain(marker)
}

async function uploadDraft(page: Page, marker: string): Promise<void> {
  await page.getByLabel("Website zip").setInputFiles(await siteZip(marker))
  await expect(page.getByTestId("website-draft")).toContainText("5 files")
}

test("a practice publishes its website, rolls it back, and it serves on its own host @portal", async ({
  browser,
  request,
}) => {
  const practice = await signInToFreshPractice(browser, "domains")
  const { page, context, api } = practice
  const first = `First ${Date.now()}`
  const second = `Second ${Date.now()}`
  try {
    await page.goto(SETTINGS_PATH)
    await uploadDraft(page, first)

    // The preview is the draft, on the API's origin, in a tab of its own.
    const opened = context.waitForEvent("page")
    await page.getByRole("button", { name: "Preview" }).click()
    const preview = await opened
    await preview.waitForLoadState()
    await expect(preview.getByRole("heading", { name: first })).toBeVisible()
    await preview.close()

    await page.getByRole("button", { name: "Publish" }).click()
    await expect(page.getByTestId("website-live")).toContainText(`Live at ${SITE}`)
    await expectLive(request, first)

    const home = await onSite(request, SITE, "/")
    expect(home.status()).toBe(200)
    expect(home.headers()["content-type"]).toBe("text/html; charset=utf-8")
    expect(home.headers()["content-security-policy"]).toContain("base-uri 'self'")
    expect(home.headers()["x-content-type-options"]).toBe("nosniff")
    expect(home.headers()["set-cookie"]).toBeUndefined()

    const css = await onSite(request, SITE, "/css/site.css")
    expect(css.status()).toBe(200)
    expect(css.headers()["content-type"]).toBe("text/css; charset=utf-8")
    expect(await css.text()).toContain("teal")

    const folder = await onSite(request, SITE, "/about")
    expect(folder.status()).toBe(301)
    expect(folder.headers()["location"]).toBe(`http://${SITE}:${PRACTICE_HOST_PORT}/about/`)
    const about = await onSite(request, SITE, "/about/")
    expect(about.status(), "a folder keeps its slash on a website host").toBe(200)
    expect(await about.text()).toContain(`About ${first}`)

    const missing = await onSite(request, SITE, "/no-such-page")
    expect(missing.status()).toBe(404)
    expect(await missing.text()).toContain("Not here")

    for (const path of ["/api/logout", "/dashboard", "/login"]) {
      const response = await onSite(request, SITE, path)
      expect(response.status(), `${path} is the website's, not the app's`).toBe(404)
      expect(await response.text()).toContain("Not here")
    }

    const www = await onSite(request, WWW, "/about/?from=card")
    expect(www.status()).toBe(301)
    expect(www.headers()["location"]).toBe(`https://${SITE}/about/?from=card`)

    // A second version, then back to the first.
    await uploadDraft(page, second)
    await page.getByRole("button", { name: "Publish" }).click()
    await expectLive(request, second)

    const versions = page.getByRole("list", { name: "Published versions" })
    const previous = versions.locator("li").filter({ hasNot: page.getByText("Current", { exact: true }) }).first()
    await previous.getByRole("button", { name: "Roll back" }).click()
    await expectLive(request, first)

    // The practice's portal host is untouched by any of it.
    await api.put("/api/portal/settings", { enabled: true })
    await api.post("/api/portal/practice-slug")
    const portal = await onSite(request, PORTAL, "/")
    expect(portal.status()).toBe(200)
    expect(await portal.text()).not.toContain(first)
  } finally {
    await context.close()
  }
})
